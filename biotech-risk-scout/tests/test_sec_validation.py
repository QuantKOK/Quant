import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.ingest import sec_validation  # noqa: E402


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
