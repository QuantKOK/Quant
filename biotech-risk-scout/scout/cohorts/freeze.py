"""Immutable source freezing and cohort manifest build/verify.

Each selected full-study API response is written once as canonical JSON under
``raw/<NCT>.json`` and hashed with SHA-256. The manifest records the cohort
version, inclusion/exclusion rules, exact query, API version + data timestamp,
retrieval timestamp, candidate count, included NCT IDs, excluded counts by
reason, per-file hashes, and an overall manifest hash. All writes are atomic and
overwrite protection is enforced by the caller via :func:`cohort_exists`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any

MANIFEST_VERSION = 1
RAW_DIR = "raw"
MANIFEST_NAME = "manifest.json"
QUEUE_NAME = "adjudication_queue.jsonl"
EXCLUSIONS_NAME = "exclusions.jsonl"
QUALITY_NAME = "data_quality_report.json"
NCT_PATTERN = re.compile(r"^NCT\d{8}$")
SNAPSHOT_PATH_PATTERN = re.compile(r"^raw/(NCT\d{8})\.json$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

NOT_REPRESENTATIVE_NOTICE = (
    "This is a capped, NCT-ordered pilot cohort selected for tooling and "
    "adjudication bootstrapping. It is NOT population-representative and must "
    "not be used for prevalence or base-rate estimates."
)


def canonical_json(value: Any) -> str:
    """Return deterministic JSON (sorted keys, compact separators)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Write bytes atomically (temp file + fsync + os.replace)."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp-{uuid.uuid4().hex}"
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        _best_effort_remove(temporary)
        raise


def atomic_write_text(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def snapshot_relpath(nct_id: str) -> str:
    if not isinstance(nct_id, str) or not NCT_PATTERN.fullmatch(nct_id):
        raise ValueError("snapshot NCT ID must match NCT followed by 8 digits")
    return f"{RAW_DIR}/{nct_id}.json"


def cohort_exists(output_dir: str) -> bool:
    """Return True if a manifest already exists in ``output_dir``."""
    return os.path.exists(os.path.join(output_dir, MANIFEST_NAME))


def freeze_snapshot(output_dir: str, nct_id: str, study: dict[str, Any]) -> tuple[str, str]:
    """Freeze one full-study response as canonical JSON. Returns (relpath, sha256)."""
    relpath = snapshot_relpath(nct_id)
    content = canonical_json(study) + "\n"
    atomic_write_text(os.path.join(output_dir, relpath), content)
    return relpath, sha256_text(content)


def safe_snapshot_path(output_dir: str, relpath: Any) -> tuple[str, str]:
    """Resolve a canonical raw snapshot path without allowing traversal."""
    if not isinstance(relpath, str):
        raise ValueError("snapshot path must be a string")
    match = SNAPSHOT_PATH_PATTERN.fullmatch(relpath)
    if not match:
        raise ValueError(f"invalid snapshot path: {relpath!r}")
    raw_root = os.path.realpath(os.path.join(output_dir, RAW_DIR))
    absolute = os.path.realpath(os.path.join(output_dir, *relpath.split("/")))
    try:
        inside_raw = os.path.commonpath([raw_root, absolute]) == raw_root
    except ValueError:
        inside_raw = False
    if not inside_raw:
        raise ValueError(f"snapshot path escapes cohort directory: {relpath!r}")
    return absolute, match.group(1)


def build_manifest(
    *,
    cohort_version: str,
    retrieval_timestamp: str,
    api_version: dict[str, Any],
    data_timestamp: str | None,
    query: dict[str, Any],
    inclusion_rules: dict[str, Any],
    exclusion_reason_codes: list[str],
    included_nct_ids: list[str],
    excluded_counts_by_reason: dict[str, int],
    excluded_study_count: int,
    files: list[dict[str, str]],
    queue_sha256: str,
    quality_report_sha256: str,
    exclusions_sha256: str,
) -> dict[str, Any]:
    """Assemble the manifest and stamp its overall hash."""
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "cohort_version": cohort_version,
        "retrieval_timestamp": retrieval_timestamp,
        "api_version": api_version,
        "data_timestamp": data_timestamp,
        "query": query,
        "inclusion_rules": inclusion_rules,
        "exclusion_reason_codes": list(exclusion_reason_codes),
        "candidate_count": len(included_nct_ids),
        "included_nct_ids": sorted(included_nct_ids),
        "excluded_study_count": excluded_study_count,
        "excluded_counts_by_reason": dict(sorted(excluded_counts_by_reason.items())),
        "files": sorted(files, key=lambda entry: entry["path"]),
        "adjudication_queue_sha256": queue_sha256,
        "data_quality_report_sha256": quality_report_sha256,
        "exclusions_sha256": exclusions_sha256,
        "not_representative_notice": NOT_REPRESENTATIVE_NOTICE,
    }
    manifest["manifest_sha256"] = manifest_hash(manifest)
    return manifest


def manifest_hash(manifest: dict[str, Any]) -> str:
    """Return the SHA-256 of the manifest excluding its own hash field."""
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return sha256_text(canonical_json(payload))


def write_manifest(output_dir: str, manifest: dict[str, Any]) -> None:
    atomic_write_text(
        os.path.join(output_dir, MANIFEST_NAME),
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )


def load_manifest(output_dir: str) -> dict[str, Any]:
    with open(os.path.join(output_dir, MANIFEST_NAME), "r", encoding="utf-8") as handle:
        return json.load(handle)


REQUIRED_MANIFEST_KEYS = (
    "manifest_version",
    "cohort_version",
    "retrieval_timestamp",
    "api_version",
    "data_timestamp",
    "query",
    "inclusion_rules",
    "candidate_count",
    "included_nct_ids",
    "excluded_counts_by_reason",
    "excluded_study_count",
    "exclusion_reason_codes",
    "files",
    "adjudication_queue_sha256",
    "data_quality_report_sha256",
    "exclusions_sha256",
    "not_representative_notice",
    "manifest_sha256",
)


def verify_manifest(output_dir: str) -> dict[str, Any]:
    """Verify manifest structure, manifest hash, and every raw snapshot hash.

    Detects missing snapshots, extra snapshots, edited snapshots, NCT
    duplication, and count inconsistencies. Returns ``{"ok", "errors", ...}``.
    """
    errors: list[str] = []
    try:
        manifest = load_manifest(output_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [f"cannot read manifest: {exc}"]}
    if not isinstance(manifest, dict):
        return {"ok": False, "errors": ["manifest must be a JSON object"]}

    missing_keys = [key for key in REQUIRED_MANIFEST_KEYS if key not in manifest]
    if missing_keys:
        errors.append(f"manifest missing required keys: {', '.join(missing_keys)}")

    stored_hash = manifest.get("manifest_sha256")
    if stored_hash != manifest_hash(manifest):
        errors.append("manifest_sha256 mismatch (manifest was edited)")

    files_value = manifest.get("files")
    files = files_value if isinstance(files_value, list) else []
    if not isinstance(files_value, list):
        errors.append("manifest files must be a list")
    manifest_raw_paths: set[str] = set()
    manifest_nct_ids: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            errors.append("malformed file entry in manifest")
            continue
        path = entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected):
            errors.append("malformed file entry in manifest")
            continue
        try:
            absolute, nct_id = safe_snapshot_path(output_dir, path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path in manifest_raw_paths:
            errors.append(f"duplicate snapshot path in manifest: {path}")
            continue
        manifest_raw_paths.add(path)
        manifest_nct_ids.add(nct_id)
        if not os.path.exists(absolute):
            errors.append(f"missing snapshot: {path}")
            continue
        with open(absolute, "rb") as handle:
            actual = sha256_bytes(handle.read())
        if actual != expected:
            errors.append(f"snapshot hash mismatch (edited): {path}")

    # Detect extra raw snapshots not present in the manifest.
    raw_dir = os.path.join(output_dir, RAW_DIR)
    if os.path.islink(raw_dir):
        errors.append("raw snapshot directory must not be a symbolic link")
    if os.path.isdir(raw_dir):
        for root, _, names in os.walk(raw_dir):
            for name in sorted(names):
                absolute = os.path.join(root, name)
                relpath = os.path.relpath(absolute, output_dir).replace(os.sep, "/")
                if relpath not in manifest_raw_paths:
                    errors.append(f"extra snapshot not in manifest: {relpath}")

    included = manifest.get("included_nct_ids", [])
    if isinstance(included, list):
        valid_included = [
            nct
            for nct in included
            if isinstance(nct, str) and NCT_PATTERN.fullmatch(nct)
        ]
        if len(valid_included) != len(included):
            errors.append("included_nct_ids contains an invalid NCT ID")
        if len(valid_included) != len(set(valid_included)):
            errors.append("included_nct_ids contains duplicates")
        if valid_included != sorted(valid_included) or len(valid_included) != len(included):
            errors.append("included_nct_ids is not sorted (reordered)")
        if manifest.get("candidate_count") != len(included):
            errors.append("candidate_count does not match included_nct_ids length")
        if len(manifest_raw_paths) != len(included):
            errors.append("number of frozen snapshots does not match included_nct_ids")
        if manifest_nct_ids != set(valid_included):
            errors.append("snapshot filenames do not match included_nct_ids")
    else:
        errors.append("included_nct_ids must be a list")

    candidate_count = manifest.get("candidate_count")
    if (
        not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count < 0
    ):
        errors.append("candidate_count must be a non-negative integer")

    for field, filename in (
        ("data_quality_report_sha256", QUALITY_NAME),
        ("exclusions_sha256", EXCLUSIONS_NAME),
    ):
        expected = manifest.get(field)
        if not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected):
            errors.append(f"{field} must be a lowercase SHA-256 digest")
            continue
        artifact_path = os.path.join(output_dir, filename)
        try:
            with open(artifact_path, "rb") as handle:
                actual = sha256_bytes(handle.read())
        except OSError as exc:
            errors.append(f"cannot read {filename}: {exc}")
            continue
        if actual != expected:
            errors.append(f"{filename} hash mismatch")

    queue_digest = manifest.get("adjudication_queue_sha256")
    if not isinstance(queue_digest, str) or not SHA256_PATTERN.fullmatch(queue_digest):
        errors.append("adjudication_queue_sha256 must be a lowercase SHA-256 digest")

    return {
        "ok": not errors,
        "errors": errors,
        "candidate_count": manifest.get("candidate_count"),
        "manifest_sha256": stored_hash,
    }


def _best_effort_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
