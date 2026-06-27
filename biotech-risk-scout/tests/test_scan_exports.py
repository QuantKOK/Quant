import csv
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from app.scan import export_rows_to_csv, export_rows_to_json, format_score_explanation, parse_ticker_text, resolve_tickers  # noqa: E402
from scout.scoring.research_priority import PriorityScore  # noqa: E402


def make_row(ticker="TEST", score=88):
    return {
        "ticker": ticker,
        "card": None,
        "priority": PriorityScore(
            score=score,
            main_reason="Near-term catalyst",
            red_flag_summary="-",
            c1_catalyst_urgency=27,
            c2_evidence_quality=18,
            c3_financial_viability=20,
            c4_dilution_structure=15,
            c5_data_confidence=8,
            c6_red_flags=0,
        ),
        "data": {
            "company_name": "Test Biotech Inc.",
            "upcoming_catalyst": "Phase 3 readout",
            "days_until_event": 45,
            "trial_count": 5,
            "active_trial_count": 3,
            "evidence_quality": "High",
            "cash": 500000000,
            "operating_cash_flow": -60000000,
            "monthly_burn": 20000000,
            "cash_runway_months": 25,
            "dilution_risk": "Low",
            "has_shelf": False,
            "has_recent_financing_form": False,
            "latest_10q": {"filing_date": "2026-05-10"},
            "latest_10k": {"filing_date": "2026-03-01"},
            "latest_8k": None,
        },
    }


def test_export_rows_to_json(tmp_path):
    output = tmp_path / "scan.json"

    export_rows_to_json([make_row()], str(output))

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["rank"] == 1
    assert payload[0]["ticker"] == "TEST"
    assert payload[0]["score"] == 88
    assert payload[0]["latest_10q_date"] == "2026-05-10"
    assert payload[0]["latest_8k_date"] is None


def test_export_rows_to_csv(tmp_path):
    output = tmp_path / "scan.csv"

    export_rows_to_csv([make_row()], str(output))

    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["rank"] == "1"
    assert rows[0]["ticker"] == "TEST"
    assert rows[0]["score"] == "88"
    assert rows[0]["main_reason"] == "Near-term catalyst"
    assert rows[0]["latest_10q_date"] == "2026-05-10"


def test_exports_respect_min_score(tmp_path):
    output = tmp_path / "filtered.json"

    export_rows_to_json([make_row("LOW", score=20), make_row("HIGH", score=80)], str(output), min_score=30)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["ticker"] == "HIGH"


def test_parse_ticker_text_handles_comments_commas_and_duplicates():
    text = """
    # Core watchlist
    mrna, vktx
    SAVA
    MRNA  # duplicate
    prax crsp
    """

    assert parse_ticker_text(text) == ["MRNA", "VKTX", "SAVA", "PRAX", "CRSP"]


def test_resolve_tickers_merges_cli_and_file_tickers(tmp_path):
    tickers_file = tmp_path / "watchlist.txt"
    tickers_file.write_text("vktx\nsava\nMRNA\n", encoding="utf-8")

    assert resolve_tickers(["mrna", "crsp"], str(tickers_file)) == ["MRNA", "CRSP", "VKTX", "SAVA"]


def test_format_score_explanation_includes_score_breakdown():
    explanation = format_score_explanation(make_row())

    assert "TEST score: 88/100" in explanation
    assert "Component Breakdown" in explanation
    assert "Catalyst urgency:" in explanation
    assert "27 / 30" in explanation
    assert "Evidence quality:" in explanation
    assert "18 / 20" in explanation
    assert "Financial viability:" in explanation
    assert "20 / 20" in explanation
    assert "Main reason: Near-term catalyst" in explanation
    assert "Company: Test Biotech Inc." in explanation
    assert "Cash: $500.0M" in explanation
    assert "Monthly burn: $20.0M" in explanation
    assert "Runway: 25.0mo" in explanation


def test_normal_export_omits_validation_data(tmp_path):
    output = tmp_path / "normal.json"
    export_rows_to_json([make_row()], str(output))
    record = json.loads(output.read_text(encoding="utf-8"))[0]
    assert "validated_sec_flags" not in record
    assert "validated_sec_filings" not in record
    assert "sec_validation_errors" not in record


def test_validated_json_is_structured_and_csv_is_compact_json(tmp_path):
    row = make_row()
    row["data"]["validated_sec_flags"] = {"has_going_concern": True}
    row["data"]["validated_sec_filings"] = [{"form": "10-Q", "matched_flags": ["has_going_concern"]}]
    row["data"]["sec_validation_errors"] = []
    json_path = tmp_path / "validated.json"
    csv_path = tmp_path / "validated.csv"
    export_rows_to_json([row], str(json_path))
    export_rows_to_csv([row], str(csv_path))
    assert json.loads(json_path.read_text(encoding="utf-8"))[0]["validated_sec_flags"]["has_going_concern"] is True
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        record = next(csv.DictReader(handle))
    assert json.loads(record["validated_sec_flags"])["has_going_concern"] is True


def test_main_forwards_sec_validation_ttl_to_scan_tickers(monkeypatch):
    import app.scan as scan_module

    captured = {}

    def fake_scan_tickers(tickers, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(scan_module, "scan_tickers", fake_scan_tickers)

    rc = scan_module.main(
        ["MRNA", "--no-table", "--validate-sec-text", "--sec-validation-cache-ttl-days", "15"]
    )

    assert rc == 0
    assert captured["sec_validation_ttl_days"] == 15
