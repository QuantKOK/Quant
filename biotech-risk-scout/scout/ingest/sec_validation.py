"""Targeted, opt-in validation of selected SEC filing metadata flags."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from scout.ingest.sec_filings import fetch_filing_document_text, scan_filing_text_flags


DEFAULT_SEC_VALIDATION_CACHE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".cache", "sec-validation.json")
)

SELECTED_FILING_GROUPS = (
    "going_concern_filings",
    "reverse_split_filings",
    "delisting_or_listing_noncompliance_filings",
    "atm_or_sales_agreement_filings",
)

VALIDATED_FLAG_NAMES = (
    "has_going_concern",
    "has_reverse_split",
    "has_delisting_or_listing_noncompliance",
    "has_atm_or_offering",
)

_CACHE_VERSION = 1
_CACHE_LOCK = threading.Lock()


def validate_selected_filing_flags(
    sec_data: dict[str, Any],
    max_documents: int = 3,
    cache_path: str | None = None,
) -> dict[str, Any]:
    """Validate a capped set of high-risk filing flags against filing text.

    When ``cache_path`` is provided, successful text scans are persisted by
    primary-document URL. Cache failures are reported but do not abort
    validation or prevent a direct filing fetch.
    """
    result: dict[str, Any] = {
        "validated_flags": {flag: False for flag in VALIDATED_FLAG_NAMES},
        "validated_filings": [],
        "validation_errors": [],
    }
    if max_documents <= 0:
        return result

    cache: dict[str, Any] = {}
    if cache_path:
        try:
            cache = _load_cache_documents(cache_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result["validation_errors"].append(
                {"cache_path": cache_path, "error": f"SEC validation cache read failed: {exc}"}
            )

    for filing in _select_filings(sec_data, max_documents):
        url = filing["primary_document_url"]
        matched_flags: list[str]
        cached = cache.get(url)
        if isinstance(cached, dict) and isinstance(cached.get("matched_flags"), list):
            matched_flags = [
                flag for flag in cached["matched_flags"] if flag in VALIDATED_FLAG_NAMES
            ]
        else:
            try:
                flags = scan_filing_text_flags(fetch_filing_document_text(url))
                matched_flags = [flag for flag in VALIDATED_FLAG_NAMES if flags.get(flag)]
            except Exception as exc:  # noqa: BLE001
                result["validation_errors"].append(
                    {"primary_document_url": url, "error": str(exc)}
                )
                continue

            if cache_path:
                try:
                    _write_cache_entry(cache_path, url, matched_flags)
                    cache[url] = {"matched_flags": matched_flags}
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    result["validation_errors"].append(
                        {"cache_path": cache_path, "error": f"SEC validation cache write failed: {exc}"}
                    )

        for flag in matched_flags:
            result["validated_flags"][flag] = True
        result["validated_filings"].append(
            {
                "form": filing.get("form"),
                "filing_date": filing.get("filing_date") or filing.get("filed"),
                "description": filing.get("description")
                or filing.get("primary_document_description"),
                "primary_document_url": url,
                "matched_flags": matched_flags,
            }
        )
    return result


def _select_filings(sec_data: dict[str, Any], max_documents: int) -> list[dict[str, Any]]:
    financing = sec_data.get("financing_recent_filings") or {}
    selected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for group in SELECTED_FILING_GROUPS:
        for filing in financing.get(group) or []:
            if not isinstance(filing, dict):
                continue
            url = filing.get("primary_document_url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            selected.append(filing)
            if len(selected) >= max_documents:
                return selected
    return selected


def _load_cache_documents(cache_path: str) -> dict[str, Any]:
    if not os.path.exists(cache_path):
        return {}
    with _CACHE_LOCK:
        with open(cache_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
        raise ValueError("unsupported SEC validation cache format")
    documents = payload.get("documents")
    if not isinstance(documents, dict):
        raise ValueError("SEC validation cache is missing documents")
    return documents


def _write_cache_entry(cache_path: str, url: str, matched_flags: list[str]) -> None:
    directory = os.path.dirname(os.path.abspath(cache_path))
    os.makedirs(directory, exist_ok=True)
    temp_path = f"{cache_path}.tmp"
    with _CACHE_LOCK:
        documents: dict[str, Any] = {}
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
                raise ValueError("unsupported SEC validation cache format")
            existing = payload.get("documents")
            if not isinstance(existing, dict):
                raise ValueError("SEC validation cache is missing documents")
            documents = existing
        documents[url] = {"matched_flags": matched_flags}
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": _CACHE_VERSION, "documents": documents},
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
        os.replace(temp_path, cache_path)
