import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.ingest.sec_filings import _classify_financing_and_structural_flags  # noqa: E402


def row(form, description="", primary="doc.htm"):
    return {
        "form": form,
        "filingDate": "2026-06-01",
        "reportDate": "2026-05-31",
        "accessionNumber": "0000000000-26-000001",
        "primaryDocument": primary,
        "primaryDocDescription": description,
    }


def test_classifies_shelf_registration_separately_from_other_financing():
    result = _classify_financing_and_structural_flags([
        row("S-3", "Shelf Registration Statement"),
    ])

    assert result["has_shelf_registration"] is True
    assert result["has_registration_statement"] is False
    assert result["has_atm_or_offering"] is False
    assert result["has_recent_financing_form"] is True
    assert result["financing_form_counts"] == {"S-3": 1}
    assert result["financing_recent_filings"]["shelf_registrations"][0]["form"] == "S-3"


def test_classifies_offering_prospectus_and_atm_keywords():
    result = _classify_financing_and_structural_flags([
        row("424B5", "Prospectus Supplement"),
        row("8-K", "At-the-market sales agreement"),
    ])

    assert result["has_shelf_registration"] is False
    assert result["has_atm_or_offering"] is True
    assert result["has_recent_financing_form"] is True
    assert result["financing_form_counts"] == {"424B5": 1, "8-K": 1}
    assert len(result["financing_recent_filings"]["offering_prospectuses"]) == 1
    assert len(result["financing_recent_filings"]["atm_or_sales_agreement_filings"]) == 1


def test_classifies_structural_red_flags():
    result = _classify_financing_and_structural_flags([
        row("8-K", "Reverse stock split approved"),
        row("10-Q", "Substantial doubt about ability to continue as a going concern"),
        row("8-K", "Nasdaq deficiency notice for minimum bid non-compliance"),
    ])

    assert result["has_reverse_split"] is True
    assert result["has_going_concern"] is True
    assert result["has_delisting_or_listing_noncompliance"] is True
    assert result["structural_red_flags"] == [
        "reverse_split",
        "going_concern",
        "delisting_or_listing_noncompliance",
    ]


def test_no_flags_for_plain_operating_filings():
    result = _classify_financing_and_structural_flags([
        row("10-Q", "Quarterly report"),
        row("8-K", "Results of operations and financial condition"),
    ])

    assert result["has_shelf_registration"] is False
    assert result["has_registration_statement"] is False
    assert result["has_atm_or_offering"] is False
    assert result["has_recent_financing_form"] is False
    assert result["structural_red_flags"] == []
    assert result["financing_form_counts"] == {}
