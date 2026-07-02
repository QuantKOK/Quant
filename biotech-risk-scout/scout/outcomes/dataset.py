"""Deterministic JSONL dataset builder, manifest writer, and verifier.

The builder reads source JSON/JSONL, validates and normalizes each record,
sorts deterministically, and writes a canonical JSONL file plus a manifest with
a SHA-256 hash of the dataset bytes. ``verify_dataset`` recomputes that hash and
re-validates every record so tampering is detected. No network access is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from scout.outcomes.schema import (
    SCHEMA_VERSION,
    SCIENTIFIC_OUTCOMES,
    HistoricalOutcomeRecord,
    OutcomeValidationError,
    validate_record,
)

MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "generated_at",
        "record_count",
        "label_counts",
        "registry_status_counts",
        "dataset_sha256",
    }
)


@dataclass
class VerificationResult:
    """Outcome of verifying a dataset against its manifest."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    record_count: int = 0
    label_counts: dict[str, int] = field(default_factory=dict)
    computed_sha256: str | None = None
    manifest_sha256: str | None = None


def load_source_records(path: str) -> list[dict[str, Any]]:
    """Load raw source records from a ``.jsonl`` or ``.json`` file."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    if path.lower().endswith((".jsonl", ".ndjson")):
        records: list[dict[str, Any]] = []
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise OutcomeValidationError(
                    f"invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc
        return records

    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return payload["records"]
    if isinstance(payload, dict):
        return [payload]
    raise OutcomeValidationError(f"unsupported source structure in {path}")


def normalize_records(raw_records: list[dict[str, Any]]) -> list[HistoricalOutcomeRecord]:
    """Validate and normalize records, rejecting duplicate record IDs."""
    validated: list[HistoricalOutcomeRecord] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_records):
        try:
            record = validate_record(raw)
        except OutcomeValidationError as exc:
            raise OutcomeValidationError(f"record #{index + 1}: {exc}") from exc
        if record.record_id in seen_ids:
            raise OutcomeValidationError(f"duplicate record_id: {record.record_id}")
        seen_ids.add(record.record_id)
        validated.append(record)
    return _sort_records(validated)


def canonical_jsonl(records: list[HistoricalOutcomeRecord]) -> str:
    """Serialize records to canonical, deterministic JSONL text."""
    lines = [
        json.dumps(record.to_json_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for record in records
    ]
    return "".join(f"{line}\n" for line in lines)


def dataset_sha256(content: str) -> str:
    """Return the SHA-256 hex digest of dataset content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def label_counts(records: list[HistoricalOutcomeRecord]) -> dict[str, int]:
    """Count records per scientific-outcome label (all labels present)."""
    counts = {label: 0 for label in SCIENTIFIC_OUTCOMES}
    for record in records:
        counts[record.scientific_outcome] += 1
    return counts


def registry_status_counts(records: list[HistoricalOutcomeRecord]) -> dict[str, int]:
    """Count records per registry status (kept separate from outcome labels)."""
    counts: dict[str, int] = {}
    for record in records:
        counts[record.registry_status] = counts.get(record.registry_status, 0) + 1
    return dict(sorted(counts.items()))


def build_manifest(
    records: list[HistoricalOutcomeRecord],
    content: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the manifest describing a dataset."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _normalize_generated_at(generated_at or _utc_now()),
        "record_count": len(records),
        "label_counts": label_counts(records),
        "registry_status_counts": registry_status_counts(records),
        "dataset_sha256": dataset_sha256(content),
    }


def build_dataset(
    source_path: str,
    dataset_path: str,
    manifest_path: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a canonical dataset and manifest from a source file.

    Returns the manifest dict. Deterministic: the same source yields identical
    dataset bytes and identical ``dataset_sha256`` regardless of run time.
    """
    _ensure_distinct_paths(source_path, dataset_path, manifest_path)
    records = normalize_records(load_source_records(source_path))
    content = canonical_jsonl(records)
    manifest = build_manifest(records, content, generated_at=generated_at)

    _write_text(dataset_path, content)
    _write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def verify_dataset(dataset_path: str, manifest_path: str) -> VerificationResult:
    """Verify a dataset against its manifest.

    Recomputes the dataset hash, re-validates every record, and re-derives the
    counts. Any mismatch (tampering, corruption, or schema drift) is reported in
    ``errors`` with ``ok=False``. Never raises for ordinary data problems.
    """
    result = VerificationResult(ok=True)
    try:
        with open(dataset_path, "rb") as handle:
            raw_bytes = handle.read()
    except OSError as exc:
        return VerificationResult(ok=False, errors=[f"cannot read dataset: {exc}"])
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return VerificationResult(ok=False, errors=[f"cannot read manifest: {exc}"])

    if not isinstance(manifest, dict):
        return VerificationResult(ok=False, errors=["manifest must be a JSON object"])
    result.errors.extend(_manifest_errors(manifest))

    result.computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    result.manifest_sha256 = manifest.get("dataset_sha256")
    if result.computed_sha256 != result.manifest_sha256:
        result.errors.append(
            "dataset SHA-256 mismatch (dataset does not match manifest; possible tampering)"
        )

    # Re-validate each line so structural or semantic tampering is also caught.
    records: list[HistoricalOutcomeRecord] = []
    seen_ids: set[str] = set()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        result.errors.append(f"dataset is not valid UTF-8: {exc}")
        text = ""
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = validate_record(json.loads(line))
        except (OutcomeValidationError, json.JSONDecodeError) as exc:
            result.errors.append(f"line {line_number}: {exc}")
            continue
        if record.record_id in seen_ids:
            result.errors.append(f"line {line_number}: duplicate record_id {record.record_id}")
            continue
        seen_ids.add(record.record_id)
        records.append(record)

    result.record_count = len(records)
    result.label_counts = label_counts(records)

    expected_count = manifest.get("record_count")
    if expected_count != result.record_count:
        result.errors.append(
            f"record_count mismatch: manifest {expected_count}, dataset {result.record_count}"
        )
    expected_labels = manifest.get("label_counts")
    if expected_labels != result.label_counts:
        result.errors.append("label_counts mismatch between manifest and dataset")
    expected_statuses = manifest.get("registry_status_counts")
    actual_statuses = registry_status_counts(records)
    if expected_statuses != actual_statuses:
        result.errors.append("registry_status_counts mismatch between manifest and dataset")

    canonical_bytes = canonical_jsonl(_sort_records(records)).encode("utf-8")
    if raw_bytes != canonical_bytes:
        result.errors.append(
            "dataset is not canonical JSONL (record order, whitespace, or blank lines differ)"
        )

    result.ok = not result.errors
    return result


def summarize_dataset(dataset_path: str) -> dict[str, Any]:
    """Return counts and the dataset hash for an existing dataset file."""
    with open(dataset_path, "rb") as handle:
        raw_bytes = handle.read()
    records = normalize_records(load_source_records(dataset_path))
    return {
        "schema_version": SCHEMA_VERSION,
        "record_count": len(records),
        "label_counts": label_counts(records),
        "registry_status_counts": registry_status_counts(records),
        "dataset_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }


def _sort_records(records: list[HistoricalOutcomeRecord]) -> list[HistoricalOutcomeRecord]:
    return sorted(
        records,
        key=lambda r: (r.nct_id, r.prediction_cutoff_at, r.outcome_observed_at, r.record_id),
    )


def _write_text(path: str, content: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    # Write beside the destination, then replace it so interrupted writes do not
    # leave a truncated dataset or manifest.
    temporary_path = f"{os.path.abspath(path)}.tmp-{uuid.uuid4()}"
    try:
        # newline="" keeps exact "\n" bytes so hashes are stable across platforms.
        with open(temporary_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_distinct_paths(source_path: str, dataset_path: str, manifest_path: str) -> None:
    resolved = [os.path.abspath(path) for path in (source_path, dataset_path, manifest_path)]
    if len(set(resolved)) != len(resolved):
        raise OutcomeValidationError(
            "source, dataset, and manifest paths must be different"
        )


def _normalize_generated_at(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutcomeValidationError("generated_at must be an ISO-8601 timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise OutcomeValidationError("generated_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise OutcomeValidationError("generated_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _manifest_errors(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    fields = set(manifest)
    missing = sorted(MANIFEST_FIELDS - fields)
    unknown = sorted(fields - MANIFEST_FIELDS)
    if missing:
        errors.append(f"manifest missing required field(s): {', '.join(missing)}")
    if unknown:
        errors.append(f"manifest has unknown field(s): {', '.join(unknown)}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"manifest schema_version must be {SCHEMA_VERSION}")
    try:
        _normalize_generated_at(manifest.get("generated_at"))
    except OutcomeValidationError as exc:
        errors.append(f"manifest {exc}")

    record_count = manifest.get("record_count")
    if not _is_nonnegative_int(record_count):
        errors.append("manifest record_count must be a non-negative integer")

    labels = manifest.get("label_counts")
    if (
        not isinstance(labels, dict)
        or set(labels) != set(SCIENTIFIC_OUTCOMES)
        or any(not _is_nonnegative_int(value) for value in labels.values())
    ):
        errors.append(
            "manifest label_counts must contain every scientific outcome "
            "with non-negative integer counts"
        )

    statuses = manifest.get("registry_status_counts")
    if (
        not isinstance(statuses, dict)
        or any(
            not isinstance(key, str)
            or not key
            or not _is_nonnegative_int(value)
            for key, value in statuses.items()
        )
    ):
        errors.append(
            "manifest registry_status_counts must map statuses to non-negative integers"
        )

    digest = manifest.get("dataset_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append("manifest dataset_sha256 must be a lowercase SHA-256 digest")
    return errors


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
