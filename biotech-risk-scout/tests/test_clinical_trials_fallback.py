import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.ingest.clinical_trials import (  # noqa: E402
    _resolve_sponsor_query,
    normalize_company_name,
)


def test_normalize_company_name_removes_state_and_corporate_suffixes():
    assert normalize_company_name("ACME THERAPEUTICS, INC. /DE") == "ACME"
    assert normalize_company_name("Example Pharmaceuticals Corp") == "Example"
    assert normalize_company_name("Rocket Biosciences Holdings") == "Rocket"


def test_resolve_sponsor_query_prefers_manual_ticker_map():
    sponsor, source = _resolve_sponsor_query("MRNA", fallback_sponsor_name="MODERNA INC")

    assert sponsor == "Moderna"
    assert source == "manual_ticker_map"


def test_resolve_sponsor_query_uses_sec_company_name_fallback_for_unknown_ticker():
    sponsor, source = _resolve_sponsor_query("XYZB", fallback_sponsor_name="XYZ BIOPHARMA, INC. /DE")

    assert sponsor == "XYZ"
    assert source == "sec_company_name_fallback"


def test_resolve_sponsor_query_falls_back_to_input_without_company_name():
    sponsor, source = _resolve_sponsor_query("Unknown Sponsor")

    assert sponsor == "Unknown Sponsor"
    assert source == "input"
