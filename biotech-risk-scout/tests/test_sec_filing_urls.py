"""Tests for SEC filing URL construction and document text keyword scanning.

No live SEC network calls are made in any of these tests.
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.ingest.sec_filings import (  # noqa: E402
    _accession_no_dashes,
    build_filing_index_url,
    build_primary_document_url,
    _summarize_row,
    scan_filing_text_flags,
)


FAKE_CIK = 1234567
FAKE_ACCESSION = "0001234567-26-000042"
FAKE_PRIMARY = "xbio-20260601.htm"


def test_accession_no_dashes():
    assert _accession_no_dashes("0001234567-26-000042") == "000123456726000042"


def test_build_filing_index_url():
    url = build_filing_index_url(FAKE_CIK, FAKE_ACCESSION)
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/0001234567"
        "/000123456726000042/0001234567-26-000042-index.html"
    )


def test_build_primary_document_url():
    url = build_primary_document_url(FAKE_CIK, FAKE_ACCESSION, FAKE_PRIMARY)
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/0001234567"
        "/000123456726000042/xbio-20260601.htm"
    )


def test_summarize_row_includes_urls_when_cik_provided():
    row = {
        "form": "10-Q",
        "filingDate": "2026-06-01",
        "reportDate": "2026-05-31",
        "accessionNumber": FAKE_ACCESSION,
        "primaryDocument": FAKE_PRIMARY,
        "primaryDocDescription": "Quarterly report",
    }
    summary = _summarize_row(row, cik=FAKE_CIK)
    assert summary["filing_index_url"] == build_filing_index_url(FAKE_CIK, FAKE_ACCESSION)
    assert summary["primary_document_url"] == build_primary_document_url(FAKE_CIK, FAKE_ACCESSION, FAKE_PRIMARY)
    # Backwards-compatible fields still present
    assert summary["form"] == "10-Q"
    assert summary["accession_number"] == FAKE_ACCESSION
    assert summary["primary_document"] == FAKE_PRIMARY


def test_summarize_row_no_urls_without_cik():
    row = {
        "form": "10-Q",
        "filingDate": "2026-06-01",
        "accessionNumber": FAKE_ACCESSION,
        "primaryDocument": FAKE_PRIMARY,
    }
    summary = _summarize_row(row)
    assert "filing_index_url" not in summary
    assert "primary_document_url" not in summary


def test_summarize_row_no_urls_when_accession_missing():
    row = {
        "form": "10-Q",
        "filingDate": "2026-06-01",
        "accessionNumber": None,
        "primaryDocument": FAKE_PRIMARY,
    }
    summary = _summarize_row(row, cik=FAKE_CIK)
    assert "filing_index_url" not in summary


def test_scan_filing_text_flags_going_concern():
    text = "There is substantial doubt about the company's ability to continue as a going concern."
    flags = scan_filing_text_flags(text)
    assert flags["has_going_concern"] is True
    assert flags["has_reverse_split"] is False
    assert flags["has_atm_or_offering"] is False
    assert flags["has_delisting_or_listing_noncompliance"] is False


def test_scan_filing_text_flags_reverse_split():
    text = "The board approved a 1-for-10 reverse stock split effective July 1, 2026."
    flags = scan_filing_text_flags(text)
    assert flags["has_reverse_split"] is True
    assert flags["has_going_concern"] is False


def test_scan_filing_text_flags_atm():
    text = "We may offer and sell shares under our at-the-market equity offering program."
    flags = scan_filing_text_flags(text)
    assert flags["has_atm_or_offering"] is True
    assert flags["has_going_concern"] is False


def test_scan_filing_text_flags_atm_sales_agreement():
    text = "We entered into a sales agreement with an agent to sell shares from time to time."
    flags = scan_filing_text_flags(text)
    assert flags["has_atm_or_offering"] is True


def test_scan_filing_text_flags_listing_noncompliance():
    text = "We received a Nasdaq deficiency notice regarding the minimum bid price requirement."
    flags = scan_filing_text_flags(text)
    assert flags["has_delisting_or_listing_noncompliance"] is True
    assert flags["has_going_concern"] is False


def test_scan_filing_text_flags_multiple():
    text = (
        "Going concern doubt noted. "
        "Company also executed a reverse split. "
        "Additionally entered into an equity distribution agreement."
    )
    flags = scan_filing_text_flags(text)
    assert flags["has_going_concern"] is True
    assert flags["has_reverse_split"] is True
    assert flags["has_atm_or_offering"] is True
    assert flags["has_delisting_or_listing_noncompliance"] is False


def test_scan_filing_text_flags_clean():
    text = "The company reported strong revenue growth and reaffirmed annual guidance."
    flags = scan_filing_text_flags(text)
    assert all(v is False for v in flags.values())
