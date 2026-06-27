import json
import os
import sys
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.ingest import sec_validation  # noqa: E402


def _write_cache(cache_path, documents):
    payload = {"version": 1, "documents": documents}
    cache_path.write_text(json.dumps(payload), encoding="utf-8")


def _fresh_iso():
    return datetime.now(timezone.utc).isoformat()


def _stale_iso(days=60):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _sec_data():
    return {"financing_recent_filings": {
        "going_concern_filings": [{"form": "10-Q", "filing_date": "2026-06-01", "primary_document_url": "https://sec.test/one"}],
        "reverse_split_filings": [{"form": "8-K", "filing_date": "2026-05-01", "primary_document_url": "https://sec.test/two"}],
        "delisting_or_listing_noncompliance_filings": [{"form": "8-K", "primary_document_url": "https://sec.test/three"}],
        "atm_or_sales_agreement_filings": [{"form": "424B5", "primary_document_url": "https://sec.test/four"}],
    }}


def test_validation_caps_fetches_and_aggregates_flags(monkeypatch):
    fetched = []
    def fake_fetch(url):
        fetched.append(url)
        return "substantial doubt and reverse stock split"
    monkeypatch.setattr(sec_validation, "fetch_filing_document_text", fake_fetch)
    result = sec_validation.validate_selected_filing_flags(_sec_data(), max_documents=2)
    assert fetched == ["https://sec.test/one", "https://sec.test/two"]
    assert result["validated_flags"]["has_going_concern"] is True
    assert result["validated_flags"]["has_reverse_split"] is True
    assert len(result["validated_filings"]) == 2


def test_validation_captures_fetch_errors(monkeypatch):
    def fail(_url):
        raise RuntimeError("offline failure")
    monkeypatch.setattr(sec_validation, "fetch_filing_document_text", fail)
    result = sec_validation.validate_selected_filing_flags(_sec_data(), max_documents=1)
    assert result["validated_filings"] == []
    assert result["validation_errors"][0]["error"] == "offline failure"


def test_validation_cache_prevents_repeat_fetch(monkeypatch, tmp_path):
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return "substantial doubt about the company's ability to continue"

    monkeypatch.setattr(sec_validation, "fetch_filing_document_text", fake_fetch)
    cache_path = tmp_path / "sec-validation.json"

    first = sec_validation.validate_selected_filing_flags(
        _sec_data(),
        max_documents=1,
        cache_path=str(cache_path),
    )
    second = sec_validation.validate_selected_filing_flags(
        _sec_data(),
        max_documents=1,
        cache_path=str(cache_path),
    )

    assert fetched == ["https://sec.test/one"]
    assert first["validated_flags"]["has_going_concern"] is True
    assert second["validated_flags"]["has_going_concern"] is True
    assert second["validation_errors"] == []


def test_corrupt_cache_falls_back_to_fetch(monkeypatch, tmp_path):
    cache_path = tmp_path / "sec-validation.json"
    cache_path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(
        sec_validation,
        "fetch_filing_document_text",
        lambda _url: "reverse stock split",
    )

    result = sec_validation.validate_selected_filing_flags(
        _sec_data(),
        max_documents=1,
        cache_path=str(cache_path),
    )

    assert result["validated_flags"]["has_reverse_split"] is True
    assert "cache read failed" in result["validation_errors"][0]["error"]
    assert "cache write failed" in result["validation_errors"][1]["error"]


def test_fresh_cache_entry_is_reused(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(
        sec_validation,
        "fetch_filing_document_text",
        lambda url: fetched.append(url) or "going concern",
    )
    cache_path = tmp_path / "sec-validation.json"
    _write_cache(
        cache_path,
        {
            "https://sec.test/one": {
                "matched_flags": ["has_going_concern"],
                "cached_at": _fresh_iso(),
                "source_url": "https://sec.test/one",
            }
        },
    )

    result = sec_validation.validate_selected_filing_flags(
        _sec_data(), max_documents=1, cache_path=str(cache_path)
    )

    assert fetched == []  # fresh entry reused, no refetch
    assert result["validated_flags"]["has_going_concern"] is True


def test_stale_cache_entry_is_refetched(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(
        sec_validation,
        "fetch_filing_document_text",
        lambda url: fetched.append(url) or "reverse stock split",
    )
    cache_path = tmp_path / "sec-validation.json"
    _write_cache(
        cache_path,
        {
            "https://sec.test/one": {
                "matched_flags": [],
                "cached_at": _stale_iso(60),
                "source_url": "https://sec.test/one",
            }
        },
    )

    sec_validation.validate_selected_filing_flags(
        _sec_data(), max_documents=1, cache_path=str(cache_path), ttl_days=30
    )

    assert fetched == ["https://sec.test/one"]  # stale -> refetched
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = payload["documents"]["https://sec.test/one"]
    assert entry["matched_flags"] == ["has_reverse_split"]
    assert entry["source_url"] == "https://sec.test/one"
    assert sec_validation.is_cache_entry_fresh(entry) is True


def test_legacy_entry_without_cached_at_is_treated_stale(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(
        sec_validation,
        "fetch_filing_document_text",
        lambda url: fetched.append(url) or "going concern",
    )
    cache_path = tmp_path / "sec-validation.json"
    _write_cache(
        cache_path,
        {"https://sec.test/one": {"matched_flags": ["has_going_concern"]}},
    )

    result = sec_validation.validate_selected_filing_flags(
        _sec_data(), max_documents=1, cache_path=str(cache_path)
    )

    assert fetched == ["https://sec.test/one"]  # legacy entry refetched
    assert result["validated_flags"]["has_going_concern"] is True
    # The rewritten entry now carries metadata.
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "cached_at" in payload["documents"]["https://sec.test/one"]


def test_prune_removes_stale_keeps_fresh(tmp_path):
    cache_path = tmp_path / "sec-validation.json"
    _write_cache(
        cache_path,
        {
            "https://sec.test/fresh": {
                "matched_flags": [],
                "cached_at": _fresh_iso(),
                "source_url": "https://sec.test/fresh",
            },
            "https://sec.test/stale": {
                "matched_flags": [],
                "cached_at": _stale_iso(60),
                "source_url": "https://sec.test/stale",
            },
            "https://sec.test/legacy": {"matched_flags": []},
        },
    )

    counts = sec_validation.prune_validation_cache(str(cache_path), ttl_days=30)

    assert counts == {"before": 3, "after": 1, "removed": 2}
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert list(payload["documents"]) == ["https://sec.test/fresh"]


def test_prune_missing_cache_is_safe(tmp_path):
    counts = sec_validation.prune_validation_cache(str(tmp_path / "absent.json"))
    assert counts == {"before": 0, "after": 0, "removed": 0}


def test_prune_corrupt_cache_is_safe(tmp_path):
    cache_path = tmp_path / "sec-validation.json"
    cache_path.write_text("not json", encoding="utf-8")

    counts = sec_validation.prune_validation_cache(str(cache_path))

    assert counts == {"before": 0, "after": 0, "removed": 0}


def test_is_cache_entry_fresh_variants():
    assert sec_validation.is_cache_entry_fresh({"cached_at": _fresh_iso()}) is True
    assert sec_validation.is_cache_entry_fresh({"cached_at": _stale_iso(40)}, ttl_days=30) is False
    assert sec_validation.is_cache_entry_fresh({}) is False
    assert sec_validation.is_cache_entry_fresh({"cached_at": "not-a-timestamp"}) is False
    assert sec_validation.is_cache_entry_fresh("not-a-dict") is False
