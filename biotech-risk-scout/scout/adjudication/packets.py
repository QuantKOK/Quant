"""Build adjudication evidence packets from frozen authoritative snapshots.

Packets are assembled *only* from the immutable authoritative cohort snapshots
(never re-fetched, never inferred). Each packet's source snapshot hash is checked
against the authoritative manifest before use, so a tampered source aborts the
build. First-pass drafts carry a null proposed outcome and ``unresolved`` status;
a second human reviewer supplies dated evidence and any proposal.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

from scout.adjudication.schema import SCHEMA_VERSION, AdjudicationError, validate_packet
from scout.cohorts.freeze import (
    MANIFEST_NAME,
    atomic_write_text,
    canonical_json,
    sha256_bytes,
    safe_snapshot_path,
)

BATCH_ID = "adjudication-batch-1"
BATCH_MANIFEST_VERSION = 1
PACKETS_NAME = "packets.jsonl"
BATCH_MANIFEST_NAME = "manifest.json"
DEFAULT_REVIEWER = "claude-first-pass-draft"

NOT_FINAL_NOTICE = (
    "First-pass adjudication drafts for a second human reviewer. Not final labels "
    "and not training data. Outcomes require dated public evidence; registry status "
    "and results availability are not outcomes."
)

CONSERVATIVE_RATIONALE = (
    "First-pass automated packet assembled from the frozen authoritative registry "
    "snapshot. No proposed outcome is asserted: mapping the pre-specified primary "
    "endpoint(s) to dated public evidence (peer-reviewed publication > "
    "ClinicalTrials.gov posted results/SAP > regulatory documents > sponsor topline) "
    "requires a second human reviewer. Registry status and results availability are "
    "not outcomes. Marked unresolved pending evidence review."
)

REVIEW_PATCH_FIELDS = frozenset(
    {
        "nct_id",
        "evidence_sources",
        "conflicts",
        "proposed_outcome",
        "adjudication_rationale",
        "confidence",
        "reviewer",
        "reviewed_at",
        "review_status",
    }
)

BATCH_ONE_NCTS = (
    "NCT00774345",
    "NCT01224106",
    "NCT01265849",
    "NCT01543490",
    "NCT01546571",
    "NCT01554618",
    "NCT01659658",
    "NCT01734928",
    "NCT01743989",
    "NCT01776840",
)


class BatchError(RuntimeError):
    """Raised on batch orchestration problems (paths, overwrite protection...)."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_packet_id(nct_id: str, batch_id: str = BATCH_ID) -> str:
    return f"{batch_id}:{nct_id}"


def extract_registry_facts(study: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract protocol primary endpoints and posted-results facts (frozen data)."""
    proto = study.get("protocolSection", {}) if isinstance(study, dict) else {}
    status = proto.get("statusModule", {})
    outcomes = proto.get("outcomesModule", {})
    results = study.get("resultsSection", {}) if isinstance(study, dict) else {}

    primary_outcomes = [
        {
            "measure": (item.get("measure") or "").strip(),
            "time_frame": (item.get("timeFrame") or "").strip(),
            "description": (item.get("description") or "").strip(),
        }
        for item in outcomes.get("primaryOutcomes", [])
        if isinstance(item, dict) and (item.get("measure") or "").strip()
    ]

    posted_measures = results.get("outcomeMeasuresModule", {}).get("outcomeMeasures", []) if results else []
    primary_titles = [
        (m.get("title") or "").strip()
        for m in posted_measures
        if isinstance(m, dict) and (m.get("type") or "").upper() == "PRIMARY" and (m.get("title") or "").strip()
    ]
    results_first_posted = status.get("resultsFirstPostDateStruct", {}).get("date")
    has_results = bool(study.get("hasResults")) or bool(results) or bool(results_first_posted)

    registry_results = {
        "has_posted_results": has_results,
        "results_first_posted_date": results_first_posted,
        "primary_outcome_measure_titles": primary_titles,
        "primary_outcome_results": [
            item
            for item in posted_measures
            if isinstance(item, dict)
            and (item.get("type") or "").upper() == "PRIMARY"
        ],
        "posted_outcome_measure_count": len(posted_measures),
    }
    return primary_outcomes, registry_results


def build_packet(
    nct_id: str,
    snapshot_path: str,
    snapshot_sha256: str,
    study: dict[str, Any],
    *,
    reviewer: str,
    reviewed_at: str,
    batch_id: str = BATCH_ID,
) -> dict[str, Any]:
    """Build one validated, conservative first-pass packet (null proposal)."""
    primary_outcomes, registry_results = extract_registry_facts(study)
    packet = {
        "packet_id": make_packet_id(nct_id, batch_id),
        "nct_id": nct_id,
        "source_snapshot_path": snapshot_path,
        "source_snapshot_sha256": snapshot_sha256,
        "primary_outcome_definitions": primary_outcomes,
        "registry_results": registry_results,
        "evidence_sources": [],
        "conflicts": [],
        "proposed_outcome": None,
        "adjudication_rationale": CONSERVATIVE_RATIONALE,
        "confidence": None,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "review_status": "unresolved",
        "historical_feature_snapshot_status": "unresolved",
        "schema_version": SCHEMA_VERSION,
    }
    return validate_packet(packet)


def load_authoritative_manifest(authoritative_dir: str) -> tuple[dict[str, Any], str]:
    """Return (manifest dict, sha256 of manifest bytes) for the authoritative cohort."""
    path = os.path.join(authoritative_dir, MANIFEST_NAME)
    with open(path, "rb") as handle:
        raw = handle.read()
    manifest = json.loads(raw.decode("utf-8"))
    return manifest, sha256_bytes(raw)


def _manifest_sha_map(manifest: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in manifest.get("files", []):
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            mapping[entry["path"]] = entry.get("sha256")
    return mapping


def build_packets(
    authoritative_dir: str,
    nct_ids: list[str],
    *,
    reviewer: str = DEFAULT_REVIEWER,
    reviewed_at: str | None = None,
    batch_id: str = BATCH_ID,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Build packets for ``nct_ids`` from frozen snapshots. Returns (packets, source_shas)."""
    reviewed_at = reviewed_at or _utc_now_iso()
    manifest, _ = load_authoritative_manifest(authoritative_dir)
    sha_map = _manifest_sha_map(manifest)

    packets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for nct_id in nct_ids:
        nct_id = nct_id.strip().upper()
        if nct_id in seen:
            raise BatchError(f"duplicate NCT ID requested: {nct_id}")
        seen.add(nct_id)
        relpath = f"raw/{nct_id}.json"
        expected_sha = sha_map.get(relpath)
        if expected_sha is None:
            raise BatchError(f"{nct_id} is not present in the authoritative manifest")
        absolute, _ = safe_snapshot_path(authoritative_dir, relpath)
        with open(absolute, "rb") as handle:
            raw = handle.read()
        actual_sha = sha256_bytes(raw)
        if actual_sha != expected_sha:
            raise BatchError(
                f"authoritative snapshot hash mismatch for {nct_id}; refusing to build from a tampered source"
            )
        study = json.loads(raw.decode("utf-8"))
        packets.append(
            build_packet(
                nct_id,
                relpath,
                actual_sha,
                study,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
                batch_id=batch_id,
            )
        )
    packets.sort(key=lambda p: p["nct_id"])
    return packets, sha_map


def render_packets_jsonl(packets: list[dict[str, Any]]) -> str:
    """Render packets as deterministic canonical JSONL sorted by NCT ID."""
    ordered = sorted(packets, key=lambda p: p["nct_id"])
    return "".join(f"{canonical_json(packet)}\n" for packet in ordered)


def build_batch_manifest(
    packets: list[dict[str, Any]],
    packets_sha256: str,
    *,
    batch_id: str,
    generated_at: str,
    reviewer: str,
    authoritative_manifest_sha256: str,
    authoritative_cohort_version: str | None,
) -> dict[str, Any]:
    ordered = sorted(packets, key=lambda p: p["nct_id"])
    manifest = {
        "manifest_version": BATCH_MANIFEST_VERSION,
        "batch_id": batch_id,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "reviewer": reviewer,
        "authoritative_manifest_sha256": authoritative_manifest_sha256,
        "authoritative_cohort_version": authoritative_cohort_version,
        "packet_count": len(ordered),
        "nct_ids": [p["nct_id"] for p in ordered],
        "packets": [
            {
                "packet_id": p["packet_id"],
                "nct_id": p["nct_id"],
                "source_snapshot_sha256": p["source_snapshot_sha256"],
            }
            for p in ordered
        ],
        "packets_sha256": packets_sha256,
        "not_final_notice": NOT_FINAL_NOTICE,
    }
    manifest["manifest_sha256"] = manifest_hash(manifest)
    return manifest


def manifest_hash(manifest: dict[str, Any]) -> str:
    payload = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def create_packets(
    authoritative_dir: str,
    output_dir: str,
    *,
    nct_ids: list[str] | None = None,
    reviewer: str = DEFAULT_REVIEWER,
    reviewed_at: str | None = None,
    force: bool = False,
    batch_id: str = BATCH_ID,
) -> dict[str, Any]:
    """Build packets and write ``packets.jsonl`` + ``manifest.json`` transactionally."""
    authoritative_dir = os.path.abspath(authoritative_dir)
    output_dir = os.path.abspath(output_dir)
    _guard_paths(authoritative_dir, output_dir)

    if _path_has_content(output_dir) and not force:
        raise BatchError(
            f"refusing to overwrite existing batch at {output_dir}; pass force=True/--force to replace it"
        )

    reviewed_at = reviewed_at or _utc_now_iso()
    targets = list(nct_ids) if nct_ids else list(BATCH_ONE_NCTS)
    packets, _ = build_packets(
        authoritative_dir, targets, reviewer=reviewer, reviewed_at=reviewed_at, batch_id=batch_id
    )
    manifest_dict, authoritative_manifest_sha = load_authoritative_manifest(authoritative_dir)

    packets_text = render_packets_jsonl(packets)
    packets_sha = sha256_bytes(packets_text.encode("utf-8"))
    manifest = build_batch_manifest(
        packets,
        packets_sha,
        batch_id=batch_id,
        generated_at=reviewed_at,
        reviewer=reviewer,
        authoritative_manifest_sha256=authoritative_manifest_sha,
        authoritative_cohort_version=manifest_dict.get("cohort_version"),
    )

    parent = os.path.dirname(output_dir)
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=f".{os.path.basename(output_dir)}.staging-", dir=parent)
    try:
        atomic_write_text(os.path.join(staging, PACKETS_NAME), packets_text)
        atomic_write_text(
            os.path.join(staging, BATCH_MANIFEST_NAME),
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        _promote_staging_directory(staging, output_dir, force=force)
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)

    return {
        "output_dir": output_dir,
        "batch_id": batch_id,
        "packet_count": len(packets),
        "nct_ids": [p["nct_id"] for p in packets],
        "packets_sha256": packets_sha,
        "manifest_sha256": manifest["manifest_sha256"],
        "reviewed_at": reviewed_at,
        "proposals": {p["nct_id"]: p["proposed_outcome"] for p in packets},
        "review_statuses": {p["nct_id"]: p["review_status"] for p in packets},
    }


def apply_review_file(
    authoritative_dir: str,
    output_dir: str,
    reviews_path: str,
    *,
    force: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Apply review-only patches to regenerated authoritative packet facts."""
    authoritative_dir = os.path.abspath(authoritative_dir)
    output_dir = os.path.abspath(output_dir)
    _guard_paths(authoritative_dir, output_dir)
    if not force:
        raise BatchError("apply-reviews requires --force to replace the existing batch")

    with open(os.path.join(output_dir, BATCH_MANIFEST_NAME), "r", encoding="utf-8") as handle:
        existing_manifest = json.load(handle)
    if not isinstance(existing_manifest, dict):
        raise BatchError("existing batch manifest must be a JSON object")
    if existing_manifest.get("manifest_sha256") != manifest_hash(existing_manifest):
        raise BatchError("existing batch manifest hash mismatch")

    patches = _load_review_patches(reviews_path)
    batch_ncts = existing_manifest.get("nct_ids")
    if not isinstance(batch_ncts, list) or not all(isinstance(item, str) for item in batch_ncts):
        raise BatchError("existing batch manifest has invalid nct_ids")
    unknown_ncts = sorted(set(patches) - set(batch_ncts))
    if unknown_ncts:
        raise BatchError(f"review file contains NCT IDs outside this batch: {', '.join(unknown_ncts)}")

    generated_at = generated_at or _utc_now_iso()
    batch_id = str(existing_manifest.get("batch_id") or BATCH_ID)
    base_packets, _ = build_packets(
        authoritative_dir,
        list(batch_ncts),
        reviewer=DEFAULT_REVIEWER,
        reviewed_at=generated_at,
        batch_id=batch_id,
    )
    reviewed_packets: list[dict[str, Any]] = []
    for packet in base_packets:
        patch = patches.get(packet["nct_id"])
        if patch is not None:
            for field in REVIEW_PATCH_FIELDS - {"nct_id"}:
                packet[field] = patch[field]
        reviewed_packets.append(validate_packet(packet))

    packets_text = render_packets_jsonl(reviewed_packets)
    packets_sha = sha256_bytes(packets_text.encode("utf-8"))
    authoritative_manifest, authoritative_manifest_sha = load_authoritative_manifest(
        authoritative_dir
    )
    manifest = build_batch_manifest(
        reviewed_packets,
        packets_sha,
        batch_id=batch_id,
        generated_at=generated_at,
        reviewer="review-file-import",
        authoritative_manifest_sha256=authoritative_manifest_sha,
        authoritative_cohort_version=authoritative_manifest.get("cohort_version"),
    )

    parent = os.path.dirname(output_dir)
    staging = tempfile.mkdtemp(
        prefix=f".{os.path.basename(output_dir)}.staging-",
        dir=parent,
    )
    try:
        atomic_write_text(os.path.join(staging, PACKETS_NAME), packets_text)
        atomic_write_text(
            os.path.join(staging, BATCH_MANIFEST_NAME),
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        _promote_staging_directory(staging, output_dir, force=True)
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)

    return {
        "output_dir": output_dir,
        "packet_count": len(reviewed_packets),
        "reviewed_count": len(patches),
        "packets_sha256": packets_sha,
        "manifest_sha256": manifest["manifest_sha256"],
    }


def _load_review_patches(path: str) -> dict[str, dict[str, Any]]:
    patches: dict[str, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                patch = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BatchError(f"review line {line_number}: invalid JSON: {exc}") from exc
            if not isinstance(patch, dict):
                raise BatchError(f"review line {line_number}: review patch must be an object")
            fields = set(patch)
            if fields != REVIEW_PATCH_FIELDS:
                missing = sorted(REVIEW_PATCH_FIELDS - fields)
                unknown = sorted(fields - REVIEW_PATCH_FIELDS)
                details = []
                if missing:
                    details.append(f"missing {', '.join(missing)}")
                if unknown:
                    details.append(f"unknown {', '.join(unknown)}")
                raise BatchError(f"review line {line_number}: {'; '.join(details)}")
            nct_id = str(patch["nct_id"]).strip().upper()
            if nct_id in patches:
                raise BatchError(f"review line {line_number}: duplicate NCT ID {nct_id}")
            patch["nct_id"] = nct_id
            patches[nct_id] = patch
    return patches


def _guard_paths(authoritative_dir: str, output_dir: str) -> None:
    auth = os.path.realpath(authoritative_dir)
    out = os.path.realpath(output_dir)
    if auth == out:
        raise BatchError("output directory must not be the authoritative cohort directory")
    try:
        if os.path.commonpath([auth, out]) == auth:
            raise BatchError("output directory must not be inside the authoritative cohort directory")
    except ValueError:
        pass  # different drives -> not nested


def _path_has_content(path: str) -> bool:
    if not os.path.exists(path):
        return False
    if not os.path.isdir(path):
        return True
    return bool(os.listdir(path))


def _promote_staging_directory(staging_dir: str, output_dir: str, *, force: bool) -> None:
    if os.path.exists(output_dir) and not _path_has_content(output_dir):
        os.rmdir(output_dir)
    if not os.path.exists(output_dir):
        os.replace(staging_dir, output_dir)
        return
    if not force:
        raise BatchError(f"refusing to overwrite existing batch at {output_dir}")
    backup = f"{output_dir}.backup-{uuid.uuid4().hex}"
    os.replace(output_dir, backup)
    try:
        os.replace(staging_dir, output_dir)
    except OSError:
        os.replace(backup, output_dir)
        raise
    shutil.rmtree(backup, ignore_errors=True)
