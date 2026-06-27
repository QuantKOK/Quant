"""Targeted, opt-in validation of selected SEC filing metadata flags."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from scout.ingest.sec_filings import fetch_filing_document_text, scan_filing_text_flags


DEFAULT_SEC_VALIDATION_CACHE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".cache", "sec-validation.json")
)

DEFAULT_CACHE_TTL_DAYS = 30

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
    ttl_days: int = DEFAULT_CACHE_TTL_DAYS,
) -> dict[str, Any]:
    """Validate a capped set of high-risk filing flags against filing text.

    When ``cache_path`` is provided, successful text scans are persisted by
    primary-document URL. A cached entry is reused only when it is still fresh
    per ``ttl_days``; stale or legacy entries (missing ``cached_at``) trigger a
    refetch that overwrites the cached entry. Cache failures are reported but do
    not abort validation or prevent a direct filing fetch.
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
        if (
            isinstance(cached, dict)
            and isinstance(cached.get("matched_flags"), list)
            and is_cache_entry_fresh(cached, ttl_days)
        ):
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
                entry = _build_cache_entry(url, matched_flags)
                try:
                    _write_cache_entry(cache_path, url, entry)
                    cache[url] = entry
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


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _build_cache_entry(url: str, matched_flags: list[str]) -> dict[str, Any]:
    """Build a cache entry with metadata for a validated filing document."""
    return {
        "matched_flags": matched_flags,
        "cached_at": _utc_now_iso(),
        "source_url": url,
    }


def is_cache_entry_fresh(entry: dict[str, Any], ttl_days: int = DEFAULT_CACHE_TTL_DAYS) -> bool:
    """Return True when a cache entry is within the TTL window.

    Entries without a parseable ``cached_at`` timestamp (including legacy entries
    written before TTL support) are treated as stale rather than raising.
    """
    if not isinstance(entry, dict):
        return False
    cached_at = entry.get("cached_at")
    if not cached_at:
        return False
    try:
        cached_dt = datetime.fromisoformat(str(cached_at))
    except (TypeError, ValueError):
        return False
    if cached_dt.tzinfo is None:
        cached_dt = cached_dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - cached_dt
    return age <= timedelta(days=ttl_days)


def prune_validation_cache(
    cache_path: str, ttl_days: int = DEFAULT_CACHE_TTL_DAYS
) -> dict[str, int]:
    """Remove stale entries from the SEC validation cache.

    Safe on a missing or corrupt cache: returns zero counts rather than raising.
    Returns ``{"before": X, "after": Y, "removed": Z}``.
    """
    try:
        documents = _load_cache_documents(cache_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"before": 0, "after": 0, "removed": 0}

    before = len(documents)
    fresh = {
        url: entry
        for url, entry in documents.items()
        if isinstance(entry, dict) and is_cache_entry_fresh(entry, ttl_days)
    }
    after = len(fresh)
    removed = before - after

    if removed:
        with _CACHE_LOCK:
            _atomic_write_payload(cache_path, fresh)

    return {"before": before, "after": after, "removed": removed}


def _atomic_write_payload(cache_path: str, documents: dict[str, Any]) -> None:
    """Atomically write the cache payload. Caller must hold ``_CACHE_LOCK``."""
    directory = os.path.dirname(os.path.abspath(cache_path))
    os.makedirs(directory, exist_ok=True)
    temp_path = f"{cache_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(
            {"version": _CACHE_VERSION, "documents": documents},
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    os.replace(temp_path, cache_path)


def _write_cache_entry(cache_path: str, url: str, entry: dict[str, Any]) -> None:
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
        documents[url] = entry
        _atomic_write_payload(cache_path, documents)
