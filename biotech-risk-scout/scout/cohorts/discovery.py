"""Cohort discovery orchestration: discover, verify, rebuild-queue, summarize.

Discovery walks NCT-sorted candidates, evaluates eligibility client-side,
freezes the first N eligible full-study responses immutably, and generates the
adjudication queue, manifest, exclusions log, and data-quality report. It never
assigns a scientific outcome and never fabricates data.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

from scout.cohorts.clinicaltrials import (
    MAX_PAGES,
    STUDIES_URL,
    VERSION_URL,
    ClinicalTrialsClient,
)
from scout.cohorts.freeze import (
    EXCLUSIONS_NAME,
    QUALITY_NAME,
    QUEUE_NAME,
    atomic_write_text,
    build_manifest,
    freeze_snapshot,
    load_manifest,
    sha256_text,
    verify_manifest,
    write_manifest,
)
from scout.cohorts.protocol import (
    COHORT_VERSION,
    DEFAULT_COHORT_CAP,
    EXCLUSION_REASONS,
    INCLUSION_RULES,
    MAX_COHORT_CAP,
    cohort_query_params,
    evaluate_study,
    extract_fields,
)
from scout.cohorts.quality import build_quality_report
from scout.cohorts.queue import (
    build_queue_record,
    reproduce_queue_text,
    verify_queue,
    write_queue,
)


class CohortError(RuntimeError):
    """Raised on cohort orchestration problems (caps, overwrite protection...)."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _path_has_content(path: str) -> bool:
    if not os.path.exists(path):
        return False
    if not os.path.isdir(path):
        return True
    return bool(os.listdir(path))


def _promote_staging_directory(staging_dir: str, output_dir: str, *, force: bool) -> None:
    """Replace the target only after a complete cohort exists in staging."""
    if os.path.exists(output_dir) and not _path_has_content(output_dir):
        os.rmdir(output_dir)
    if not os.path.exists(output_dir):
        os.replace(staging_dir, output_dir)
        return
    if not force:
        raise CohortError(f"refusing to overwrite existing cohort at {output_dir}")

    backup = f"{output_dir}.backup-{uuid.uuid4().hex}"
    os.replace(output_dir, backup)
    try:
        os.replace(staging_dir, output_dir)
    except OSError:
        os.replace(backup, output_dir)
        raise
    if os.path.isdir(backup) and not os.path.islink(backup):
        shutil.rmtree(backup)
    else:
        os.remove(backup)


def clamp_cap(max_studies: int) -> int:
    """Clamp the requested cap to [1, MAX_COHORT_CAP]; reject values over the bound."""
    if max_studies < 1:
        raise CohortError("--max-studies must be at least 1")
    if max_studies > MAX_COHORT_CAP:
        raise CohortError(f"--max-studies exceeds the hard upper bound of {MAX_COHORT_CAP}")
    return max_studies


def discover_cohort(
    client: ClinicalTrialsClient,
    output_dir: str,
    *,
    max_studies: int = DEFAULT_COHORT_CAP,
    force: bool = False,
    retrieval_timestamp: str | None = None,
    page_size: int = 50,
    scan_page_limit: int | None = None,
) -> dict[str, Any]:
    """Discover, freeze, and queue the pilot cohort. Returns a public summary.

    Because the API cannot sort by NCT ID, candidates are scanned (optionally
    bounded by ``scan_page_limit``), then eligible NCT IDs are sorted ascending
    and the first ``max_studies`` are frozen. A full authoritative build leaves
    ``scan_page_limit`` unset; a small limit is only for bounded smoke checks.
    """
    cap = clamp_cap(max_studies)
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 1000:
        raise CohortError("page_size must be between 1 and 1000")
    if scan_page_limit is not None and (
        not isinstance(scan_page_limit, int)
        or isinstance(scan_page_limit, bool)
        or not 1 <= scan_page_limit <= MAX_PAGES
    ):
        raise CohortError(f"scan_page_limit must be between 1 and {MAX_PAGES}")
    output_dir = os.path.abspath(output_dir)
    if _path_has_content(output_dir) and not force:
        raise CohortError(
            f"refusing to overwrite existing cohort at {output_dir}; pass force=True/--force to replace it"
        )

    parent = os.path.dirname(output_dir)
    os.makedirs(parent, exist_ok=True)
    staging_dir = tempfile.mkdtemp(
        prefix=f".{os.path.basename(output_dir)}.staging-",
        dir=parent,
    )
    try:
        retrieval_timestamp = retrieval_timestamp or _utc_now_iso()
        version = client.get_version()
        data_timestamp = version.get("dataTimestamp")
        params = cohort_query_params(page_size)

        examined_by_nct: dict[str, dict[str, Any]] = {}
        anonymous_evaluations: list[dict[str, Any]] = []
        source_duplicate_nct_count = 0
        for study in client.iter_studies(params, max_pages=scan_page_limit):
            evaluation = evaluate_study(study)
            nct_id = evaluation["nct_id"]
            if nct_id:
                if nct_id in examined_by_nct:
                    source_duplicate_nct_count += 1
                examined_by_nct[nct_id] = evaluation
            else:
                anonymous_evaluations.append(evaluation)

        eligible_sorted = sorted(
            nct_id
            for nct_id, evaluation in examined_by_nct.items()
            if evaluation["eligible"]
        )

        files: list[dict[str, str]] = []
        queue_records: list[dict[str, Any]] = []
        included_fields: list[dict[str, Any]] = []
        selected_nct_ids: list[str] = []
        for nct_id in eligible_sorted:
            if len(selected_nct_ids) >= cap:
                break
            full_study = client.fetch_full_study(nct_id)
            full_evaluation = evaluate_study(full_study)
            if full_evaluation["nct_id"] != nct_id:
                raise CohortError(
                    f"full-study response NCT ID {full_evaluation['nct_id']!r} "
                    f"does not match requested {nct_id}"
                )
            examined_by_nct[nct_id] = full_evaluation
            if not full_evaluation["eligible"]:
                continue
            relpath, snapshot_hash = freeze_snapshot(staging_dir, nct_id, full_study)
            files.append({"path": relpath, "sha256": snapshot_hash})
            queue_records.append(build_queue_record(full_study, relpath, snapshot_hash))
            included_fields.append(full_evaluation["fields"])
            selected_nct_ids.append(nct_id)

        examined = list(examined_by_nct.values()) + anonymous_evaluations
        excluded = [item for item in examined if not item["eligible"]]
        excluded_counts_by_reason = {reason: 0 for reason in EXCLUSION_REASONS}
        for item in excluded:
            for reason in item["exclusion_reasons"]:
                excluded_counts_by_reason[reason] = (
                    excluded_counts_by_reason.get(reason, 0) + 1
                )

        queue_sha = write_queue(staging_dir, queue_records)
        report = build_quality_report(
            examined,
            included_fields,
            generated_at=retrieval_timestamp,
            bounded_scan=scan_page_limit is not None,
            source_duplicate_nct_count=source_duplicate_nct_count,
        )
        report_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
        atomic_write_text(os.path.join(staging_dir, QUALITY_NAME), report_text)
        report_sha = sha256_text(report_text)
        exclusions_sha = _write_exclusions(staging_dir, excluded)

        manifest = build_manifest(
            cohort_version=COHORT_VERSION,
            retrieval_timestamp=retrieval_timestamp,
            api_version=version,
            data_timestamp=data_timestamp,
            query={
                "version_endpoint": VERSION_URL,
                "studies_endpoint": STUDIES_URL,
                "search_params": params,
                "requested_cap": cap,
                "scan_page_limit": scan_page_limit,
                "selection_scope": (
                    "bounded_scan_window"
                    if scan_page_limit is not None
                    else "full_query"
                ),
            },
            inclusion_rules=INCLUSION_RULES,
            exclusion_reason_codes=list(EXCLUSION_REASONS),
            included_nct_ids=selected_nct_ids,
            excluded_counts_by_reason=excluded_counts_by_reason,
            excluded_study_count=len(excluded),
            files=files,
            queue_sha256=queue_sha,
            quality_report_sha256=report_sha,
            exclusions_sha256=exclusions_sha,
        )
        write_manifest(staging_dir, manifest)
        _promote_staging_directory(staging_dir, output_dir, force=force)

        eligible_found = sum(1 for item in examined if item["eligible"])
        return {
            "output_dir": output_dir,
            "cohort_version": COHORT_VERSION,
            "data_timestamp": data_timestamp,
            "retrieval_timestamp": retrieval_timestamp,
            "examined_count": len(examined),
            "eligible_found": eligible_found,
            "candidate_count": len(selected_nct_ids),
            "included_nct_ids": selected_nct_ids,
            "excluded_counts_by_reason": {
                k: v for k, v in sorted(excluded_counts_by_reason.items()) if v
            },
            "manifest_sha256": manifest["manifest_sha256"],
            "severity_summary": report["severity_summary"],
            "selection_scope": manifest["query"]["selection_scope"],
            "cap": cap,
        }
    finally:
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)


def verify_cohort(output_dir: str) -> dict[str, Any]:
    """Verify manifest, snapshot hashes, and byte-for-byte queue reproduction."""
    output_dir = os.path.abspath(output_dir)
    manifest_result = verify_manifest(output_dir)
    errors = list(manifest_result["errors"])

    queue_result: dict[str, Any] = {"ok": False, "errors": ["manifest unreadable; queue not checked"]}
    try:
        manifest = load_manifest(output_dir)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read manifest for queue verification: {exc}")
        manifest = None
    if manifest is not None:
        queue_result = verify_queue(output_dir, manifest)
        errors.extend(queue_result["errors"])

    return {
        "ok": not errors,
        "errors": errors,
        "manifest": manifest_result,
        "queue": queue_result,
    }


def rebuild_queue(output_dir: str) -> dict[str, Any]:
    """Deterministically rebuild the queue from frozen snapshots (idempotent)."""
    output_dir = os.path.abspath(output_dir)
    verification = verify_manifest(output_dir)
    if not verification["ok"]:
        raise CohortError(
            "cannot rebuild queue from an invalid cohort: "
            + "; ".join(verification["errors"])
        )
    manifest = load_manifest(output_dir)
    text = reproduce_queue_text(output_dir, manifest)
    atomic_write_text(os.path.join(output_dir, QUEUE_NAME), text)
    rebuilt_hash = sha256_text(text)
    return {
        "ok": True,
        "record_count": text.count("\n"),
        "queue_sha256": rebuilt_hash,
        "matches_manifest": rebuilt_hash == manifest.get("adjudication_queue_sha256"),
    }


def summarize_cohort(output_dir: str) -> dict[str, Any]:
    """Return a public, non-sensitive summary. Never prints raw payloads."""
    output_dir = os.path.abspath(output_dir)
    manifest = load_manifest(output_dir)
    report_path = os.path.join(output_dir, QUALITY_NAME)
    severity_summary: dict[str, Any] = {}
    exclusion_reason_counts: dict[str, Any] = {}
    if os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        severity_summary = report.get("severity_summary", {})
        exclusion_reason_counts = report.get("exclusion_reason_counts", {})

    return {
        "cohort_version": manifest.get("cohort_version"),
        "data_timestamp": manifest.get("data_timestamp"),
        "retrieval_timestamp": manifest.get("retrieval_timestamp"),
        "candidate_count": manifest.get("candidate_count"),
        "excluded_study_count": manifest.get("excluded_study_count"),
        "excluded_counts_by_reason": {
            k: v for k, v in manifest.get("excluded_counts_by_reason", {}).items() if v
        },
        "exclusion_reason_counts": exclusion_reason_counts,
        "severity_summary": severity_summary,
        "included_nct_ids": manifest.get("included_nct_ids", []),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "not_representative_notice": manifest.get("not_representative_notice"),
    }


def _write_exclusions(output_dir: str, excluded: list[dict[str, Any]]) -> str:
    ordered = sorted(excluded, key=lambda item: item.get("nct_id") or "")
    lines = []
    for item in ordered:
        lines.append(
            json.dumps(
                {"nct_id": item.get("nct_id"), "exclusion_reasons": item.get("exclusion_reasons", [])},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    text = "".join(f"{line}\n" for line in lines)
    atomic_write_text(os.path.join(output_dir, EXCLUSIONS_NAME), text)
    return sha256_text(text)
