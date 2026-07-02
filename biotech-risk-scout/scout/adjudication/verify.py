"""Verify an adjudication batch against its manifest and the authoritative cohort.

Checks packet schema, NCT/packet-id uniqueness, evidence URL/date validity (via
schema), frozen snapshot hashes against the authoritative manifest, deterministic
canonical JSONL reproduction, manifest + packet hashes, and structural drift
(missing / extra / edited / duplicate / reordered). Confirms no final label or
training-ready status and that non-null proposals are evidence-backed
``needs_second_review`` drafts. Never raises for ordinary data problems.
"""

from __future__ import annotations

import json
import os
from typing import Any

from scout.adjudication.packets import (
    BATCH_MANIFEST_NAME,
    PACKETS_NAME,
    load_authoritative_manifest,
    manifest_hash,
    render_packets_jsonl,
)
from scout.adjudication.schema import AdjudicationError, validate_packet
from scout.cohorts.freeze import safe_snapshot_path, sha256_bytes, verify_manifest


def verify_batch(output_dir: str, authoritative_dir: str) -> dict[str, Any]:
    """Verify a batch directory. Returns ``{"ok", "errors", "packet_count"}``."""
    output_dir = os.path.abspath(output_dir)
    errors: list[str] = []

    try:
        with open(os.path.join(output_dir, BATCH_MANIFEST_NAME), "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "errors": [f"cannot read batch manifest: {exc}"], "packet_count": 0}
    if not isinstance(manifest, dict):
        return {"ok": False, "errors": ["batch manifest must be a JSON object"], "packet_count": 0}

    try:
        with open(os.path.join(output_dir, PACKETS_NAME), "rb") as handle:
            packets_bytes = handle.read()
    except OSError as exc:
        return {"ok": False, "errors": [f"cannot read packets.jsonl: {exc}"], "packet_count": 0}

    # Manifest integrity.
    if manifest.get("manifest_sha256") != manifest_hash(manifest):
        errors.append("manifest_sha256 mismatch (manifest edited)")
    if manifest.get("packets_sha256") != sha256_bytes(packets_bytes):
        errors.append("packets_sha256 mismatch (packets.jsonl edited)")

    # Parse + schema-validate each packet.
    validated: list[dict[str, Any]] = []
    seen_ncts: set[str] = set()
    seen_ids: set[str] = set()
    try:
        packets_text = packets_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {
            "ok": False,
            "errors": [f"packets.jsonl is not valid UTF-8: {exc}"],
            "packet_count": 0,
        }
    for line_number, line in enumerate(packets_text.splitlines(), start=1):
        if not line.strip():
            errors.append(f"line {line_number}: blank line not allowed")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc}")
            continue
        try:
            packet = validate_packet(record)
        except AdjudicationError as exc:
            errors.append(f"line {line_number}: {exc}")
            continue
        if packet["nct_id"] in seen_ncts:
            errors.append(f"line {line_number}: duplicate nct_id {packet['nct_id']}")
        else:
            seen_ncts.add(packet["nct_id"])
        if packet["packet_id"] in seen_ids:
            errors.append(f"line {line_number}: duplicate packet_id {packet['packet_id']}")
        else:
            seen_ids.add(packet["packet_id"])
        validated.append(packet)

    # Deterministic canonical reproduction (also catches reordering/edits).
    if validated and render_packets_jsonl(validated).encode("utf-8") != packets_bytes:
        errors.append("packets.jsonl is not canonical/deterministically ordered (edited or reordered)")

    # Structural drift vs manifest packet list.
    manifest_ncts_value = manifest.get("nct_ids", [])
    manifest_ncts = (
        manifest_ncts_value
        if isinstance(manifest_ncts_value, list)
        and all(isinstance(item, str) for item in manifest_ncts_value)
        else []
    )
    if manifest_ncts is not manifest_ncts_value:
        errors.append("manifest nct_ids must be a list of strings")
    on_disk_ncts = [p["nct_id"] for p in validated]
    for missing in sorted(set(manifest_ncts) - set(on_disk_ncts)):
        errors.append(f"missing packet declared in manifest: {missing}")
    for extra in sorted(set(on_disk_ncts) - set(manifest_ncts)):
        errors.append(f"extra packet not in manifest: {extra}")
    if manifest.get("packet_count") not in (None, len(validated)):
        errors.append("packet_count does not match packets.jsonl")

    manifest_packets = manifest.get("packets", [])
    if not isinstance(manifest_packets, list):
        errors.append("manifest packets must be a list")
        manifest_packets = []
    manifest_sha_by_nct = {
        entry.get("nct_id"): entry.get("source_snapshot_sha256")
        for entry in manifest_packets
        if isinstance(entry, dict)
    }

    # Frozen snapshot hashes must match the authoritative manifest.
    auth_sha_by_path: dict[str, Any] = {}
    authoritative_verification = verify_manifest(authoritative_dir)
    if not authoritative_verification["ok"]:
        errors.extend(
            f"authoritative cohort invalid: {error}"
            for error in authoritative_verification["errors"]
        )
    try:
        auth_manifest, auth_manifest_sha = load_authoritative_manifest(authoritative_dir)
        if manifest.get("authoritative_manifest_sha256") != auth_manifest_sha:
            errors.append(
                "authoritative_manifest_sha256 does not match the current authoritative manifest"
            )
        auth_sha_by_path = {
            entry.get("path"): entry.get("sha256")
            for entry in auth_manifest.get("files", [])
            if isinstance(entry, dict)
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"cannot read authoritative manifest: {exc}")

    for packet in validated:
        nct = packet["nct_id"]
        # manifest entry consistency
        if manifest_sha_by_nct.get(nct) != packet["source_snapshot_sha256"]:
            errors.append(f"{nct}: manifest packet hash does not match packet source_snapshot_sha256")
        # authoritative cross-check
        if auth_sha_by_path:
            expected = auth_sha_by_path.get(packet["source_snapshot_path"])
            if expected is None:
                errors.append(f"{nct}: source snapshot not present in authoritative manifest")
            elif expected != packet["source_snapshot_sha256"]:
                errors.append(f"{nct}: source snapshot hash does not match authoritative manifest")
            try:
                snapshot_path, snapshot_nct = safe_snapshot_path(
                    authoritative_dir,
                    packet["source_snapshot_path"],
                )
                with open(snapshot_path, "rb") as handle:
                    actual_snapshot_sha = sha256_bytes(handle.read())
            except (OSError, ValueError) as exc:
                errors.append(f"{nct}: cannot verify authoritative snapshot bytes: {exc}")
            else:
                if snapshot_nct != nct:
                    errors.append(f"{nct}: source snapshot path references {snapshot_nct}")
                if actual_snapshot_sha != packet["source_snapshot_sha256"]:
                    errors.append(
                        f"{nct}: actual source snapshot bytes do not match packet hash"
                    )
        # historical-feature leakage guard (schema enforces, re-assert defensively)
        if packet["historical_feature_snapshot_status"] != "unresolved":
            errors.append(f"{nct}: historical_feature_snapshot_status must remain 'unresolved'")
        # non-null proposal must be an evidence-backed second-review draft
        if packet["proposed_outcome"] is not None:
            if packet["review_status"] != "needs_second_review":
                errors.append(f"{nct}: non-null proposal must be needs_second_review")
            if packet["confidence"] is None:
                errors.append(f"{nct}: non-null proposal must have a confidence")
            if not packet["evidence_sources"]:
                errors.append(f"{nct}: non-null proposal must cite dated evidence")

    return {"ok": not errors, "errors": errors, "packet_count": len(validated)}
