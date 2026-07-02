"""Human-adjudication queue built from frozen registry snapshots.

The queue is intentionally a *separate* schema from the final
``HistoricalOutcomeRecord``. It carries pending review state and never contains a
scientific outcome label. Every entry is stamped
``historical_feature_snapshot_status: "unresolved"`` because current registry
records may contain post-outcome edits and are not leakage-free features.

The queue is fully reproducible byte-for-byte from the frozen snapshots, which
lets verification detect any missing, extra, edited, or reordered record.
"""

from __future__ import annotations

import json
import os
from typing import Any

from scout.cohorts.freeze import (
    QUEUE_NAME,
    RAW_DIR,
    atomic_write_text,
    canonical_json,
    safe_snapshot_path,
    sha256_bytes,
)
from scout.cohorts.protocol import extract_fields, registry_url

QUEUE_SCHEMA_VERSION = "1.0.0"


def build_queue_record(study: dict[str, Any], snapshot_path: str, snapshot_sha256: str) -> dict[str, Any]:
    """Build one pending adjudication-queue record from a frozen study.

    Contains no scientific outcome label. ``scientific_outcome_status`` is only a
    workflow marker (``unadjudicated``); the outcome itself requires later human
    adjudication against dated public evidence.
    """
    fields = extract_fields(study)
    return {
        "queue_schema_version": QUEUE_SCHEMA_VERSION,
        "nct_id": fields["nct_id"],
        "title": fields["title"],
        "sponsor": fields["lead_sponsor"],
        "interventions": fields["interventions"],
        "conditions": fields["conditions"],
        "phase": "/".join(fields["phases"]),
        "enrollment": fields["enrollment"],
        "primary_completion_date": fields["primary_completion_date"],
        "registry_status": fields["overall_status"],
        "primary_outcomes": fields["primary_outcomes"],
        "results_first_posted_date": fields["results_first_posted_date"],
        "registry_url": registry_url(fields["nct_id"]),
        "raw_snapshot_path": snapshot_path,
        "raw_snapshot_sha256": snapshot_sha256,
        "review_status": "pending",
        "historical_feature_snapshot_status": "unresolved",
        "scientific_outcome_status": "unadjudicated",
        "evidence_candidates": [],
        "adjudication_notes": "",
    }


def render_queue(records: list[dict[str, Any]]) -> str:
    """Render queue records as deterministic canonical JSONL, sorted by NCT ID."""
    ordered = sorted(records, key=lambda record: record["nct_id"])
    return "".join(f"{canonical_json(record)}\n" for record in ordered)


def write_queue(output_dir: str, records: list[dict[str, Any]]) -> str:
    """Write the queue JSONL atomically. Returns the SHA-256 of the queue bytes."""
    text = render_queue(records)
    atomic_write_text(os.path.join(output_dir, QUEUE_NAME), text)
    return sha256_bytes(text.encode("utf-8"))


def reproduce_queue_text(output_dir: str, manifest: dict[str, Any]) -> str:
    """Rebuild the queue text purely from frozen snapshots listed in the manifest."""
    records: list[dict[str, Any]] = []
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict):
            raise ValueError("manifest contains a malformed snapshot entry")
        path = entry.get("path")
        absolute, _ = safe_snapshot_path(output_dir, path)
        with open(absolute, "rb") as handle:
            raw = handle.read()
        study = json.loads(raw.decode("utf-8"))
        if not isinstance(study, dict):
            raise ValueError(f"snapshot {path} must contain a JSON object")
        records.append(build_queue_record(study, path, sha256_bytes(raw)))
    return render_queue(records)


def verify_queue(output_dir: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Verify the on-disk queue reproduces byte-for-byte and stays label-free."""
    errors: list[str] = []
    queue_path = os.path.join(output_dir, QUEUE_NAME)
    try:
        with open(queue_path, "rb") as handle:
            on_disk = handle.read()
    except OSError as exc:
        return {"ok": False, "errors": [f"cannot read adjudication queue: {exc}"]}

    try:
        reproduced = reproduce_queue_text(output_dir, manifest).encode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # A missing/unreadable/corrupt snapshot is reported by manifest verification;
        # here we simply cannot reproduce the queue from frozen sources.
        return {"ok": False, "errors": [f"cannot reproduce queue from frozen snapshots: {exc}"]}
    if on_disk != reproduced:
        errors.append("adjudication queue does not reproduce from frozen snapshots (edited/reordered)")

    expected_hash = manifest.get("adjudication_queue_sha256")
    if expected_hash is not None and sha256_bytes(on_disk) != expected_hash:
        errors.append("adjudication_queue_sha256 mismatch with manifest")

    seen: set[str] = set()
    count = 0
    try:
        queue_text = on_disk.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {"ok": False, "errors": [f"adjudication queue is not valid UTF-8: {exc}"]}
    for line_number, line in enumerate(queue_text.splitlines(), start=1):
        if not line.strip():
            continue
        count += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"queue line {line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            errors.append(f"queue line {line_number}: record must be a JSON object")
            continue
        if "scientific_outcome" in record:
            errors.append(f"queue line {line_number}: must not contain a scientific_outcome label")
        if record.get("review_status") != "pending":
            errors.append(f"queue line {line_number}: review_status must be 'pending'")
        if record.get("historical_feature_snapshot_status") != "unresolved":
            errors.append(
                f"queue line {line_number}: historical_feature_snapshot_status must be 'unresolved'"
            )
        nct = record.get("nct_id")
        if nct in seen:
            errors.append(f"queue line {line_number}: duplicate nct_id {nct}")
        elif isinstance(nct, str):
            seen.add(nct)

    included = manifest.get("candidate_count")
    if isinstance(included, int) and included != count:
        errors.append(f"queue record count {count} does not match candidate_count {included}")

    return {"ok": not errors, "errors": errors, "record_count": count}
