import json
from pathlib import Path

import pytest

import macroedge.adapters.kalshi as kalshi
from macroedge.adapters.kalshi import (
    KALSHI_SETTLEMENT_SOURCE,
    KalshiAdapterError,
    build_contract_record_from_kalshi,
    kalshi_market_to_contract_draft,
)
from macroedge.contracts import build_contract_record, verify_contract_record


CPI = Path("macroedge/examples/kalshi-market-cpi.example.json")
FED = Path("macroedge/examples/kalshi-market-fed.example.json")
UNEMPLOYMENT = Path("macroedge/examples/kalshi-market-unemployment.example.json")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_kalshi_cpi_market_maps_to_contract_draft_and_record():
    draft = kalshi_market_to_contract_draft(load(CPI))

    assert draft["event"]["event_type"] == "cpi"
    assert draft["event"]["settlement_source"] == "Bureau of Labor Statistics CPI release"
    assert "official BLS" in draft["event"]["settlement_rules"]
    assert draft["market"]["platform"] == "Kalshi"
    assert draft["market"]["contract_id"] == "KXCPIYOY-26JUN-T3.0"
    assert draft["market"]["market_url"] == "https://kalshi.com/markets/KXCPIYOY-26JUN-T3.0"
    assert draft["prices"]["yes_bid"] == 0.40
    assert draft["prices"]["yes_ask"] == 0.44
    assert draft["prices"]["last_price"] == 0.42

    record = build_contract_record(draft, observation_id="kalshi-cpi")
    assert record["prices"]["midpoint_probability"] == 0.42
    assert len(record["contract_hash"]) == 64
    assert verify_contract_record(record)["ok"] is True


def test_kalshi_fed_market_normalizes_integer_cents():
    draft = kalshi_market_to_contract_draft(load(FED))

    assert draft["event"]["event_type"] == "fed_decision"
    assert draft["prices"]["yes_bid"] == 0.32
    assert draft["prices"]["yes_ask"] == 0.36
    assert draft["prices"]["last_price"] == 0.34


def test_kalshi_unemployment_market_normalizes_cent_strings():
    draft = kalshi_market_to_contract_draft(load(UNEMPLOYMENT))

    assert draft["event"]["event_type"] == "unemployment"
    assert draft["prices"]["yes_bid"] == 0.45
    assert draft["prices"]["yes_ask"] == 0.49
    assert draft["prices"]["last_price"] == 0.47


def test_kalshi_adapter_requires_event_type_without_guessing():
    raw = load(CPI)
    raw.pop("macroedge_event_type")

    with pytest.raises(KalshiAdapterError, match="event_type is required"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_accepts_explicit_event_type_override():
    raw = load(CPI)
    raw.pop("macroedge_event_type")

    draft = kalshi_market_to_contract_draft(raw, event_type="financial_macro")

    assert draft["event"]["event_type"] == "financial_macro"


def test_kalshi_adapter_defaults_to_venue_settlement_source():
    raw = load(CPI)
    raw.pop("macroedge_settlement_source")

    draft = kalshi_market_to_contract_draft(raw)

    assert draft["event"]["settlement_source"] == KALSHI_SETTLEMENT_SOURCE


def test_kalshi_adapter_rejects_missing_rules():
    raw = load(CPI)
    raw.pop("rules_primary")

    with pytest.raises(KalshiAdapterError, match="rules_primary"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_rejects_missing_observed_at():
    raw = load(CPI)
    raw.pop("observed_at")
    raw.pop("updated_time", None)

    with pytest.raises(KalshiAdapterError, match="observed_at"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_rejects_missing_prices():
    raw = load(CPI)
    raw.pop("yes_bid_dollars")
    raw.pop("yes_ask_dollars")
    raw.pop("last_price_dollars")

    with pytest.raises(KalshiAdapterError, match="required"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_builds_valid_contract_record_directly():
    record = build_contract_record_from_kalshi(
        load(CPI),
        observation_id="kalshi-record",
    )

    assert record["observation_id"] == "kalshi-record"
    assert record["market"]["platform"] == "Kalshi"
    assert verify_contract_record(record)["ok"] is True
    assert len(record["contract_hash"]) == 64


def test_kalshi_adapter_settlement_datetime_precedence():
    raw = load(CPI)
    raw["expiration_time"] = "2026-07-15T10:00:00-04:00"
    raw["expected_expiration_time"] = "2026-07-15T11:00:00-04:00"
    raw["latest_expiration_time"] = "2026-07-15T12:00:00-04:00"

    draft = kalshi_market_to_contract_draft(raw)

    assert draft["event"]["settlement_datetime"] == "2026-07-15T10:00:00-04:00"


def test_kalshi_adapter_uses_expected_before_latest_expiration():
    raw = load(CPI)
    raw.pop("expiration_time", None)
    raw["expected_expiration_time"] = "2026-07-15T11:00:00-04:00"
    raw["latest_expiration_time"] = "2026-07-15T12:00:00-04:00"

    draft = kalshi_market_to_contract_draft(raw)

    assert draft["event"]["settlement_datetime"] == "2026-07-15T11:00:00-04:00"


def test_kalshi_adapter_never_uses_close_time_as_settlement_datetime():
    raw = load(CPI)
    raw.pop("expiration_time", None)
    raw.pop("expected_expiration_time", None)
    raw.pop("latest_expiration_time", None)

    with pytest.raises(KalshiAdapterError, match="expiration_time"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_never_uses_close_time_as_release_datetime():
    raw = load(CPI)
    raw.pop("macroedge_release_datetime", None)
    raw.pop("occurrence_datetime", None)

    with pytest.raises(KalshiAdapterError, match="occurrence_datetime"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_treats_endpoint_prices_as_absent():
    raw = load(CPI)
    raw["yes_bid_dollars"] = "0.00"
    raw["yes_ask_dollars"] = "1.00"
    raw["last_price_dollars"] = "0.37"

    draft = kalshi_market_to_contract_draft(raw)

    assert draft["prices"]["yes_bid"] is None
    assert draft["prices"]["yes_ask"] is None
    assert draft["prices"]["last_price"] == 0.37


def test_kalshi_adapter_rejects_all_endpoint_prices():
    raw = load(CPI)
    raw["yes_bid_dollars"] = "0.00"
    raw["yes_ask_dollars"] = "1.00"
    raw["last_price_dollars"] = "1.00"

    with pytest.raises(KalshiAdapterError, match="required"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_rejects_crossed_book():
    raw = load(CPI)
    raw["yes_bid_dollars"] = "0.55"
    raw["yes_ask_dollars"] = "0.45"

    with pytest.raises(KalshiAdapterError, match="yes_bid"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_rejects_non_numeric_price():
    raw = load(CPI)
    raw["last_price_dollars"] = "not-a-price"

    with pytest.raises(KalshiAdapterError, match="last_price_dollars"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_last_only_uses_last_price_source():
    raw = load(CPI)
    raw.pop("yes_bid_dollars")
    raw.pop("yes_ask_dollars")

    record = build_contract_record_from_kalshi(raw, observation_id="kalshi-last-only")

    assert record["prices"]["implied_probability_source"] == "last_price"


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("ticker", "ticker"),
        ("title", "title"),
        ("rules_primary", "rules_primary"),
        ("occurrence_datetime", "occurrence_datetime"),
        ("observed_at", "observed_at"),
    ],
)
def test_kalshi_adapter_rejects_required_missing_fields(field, message):
    raw = load(CPI)
    raw.pop(field, None)

    with pytest.raises(KalshiAdapterError, match=message):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_rejects_missing_all_expiration_fields():
    raw = load(CPI)
    raw.pop("expiration_time", None)
    raw.pop("expected_expiration_time", None)
    raw.pop("latest_expiration_time", None)

    with pytest.raises(KalshiAdapterError, match="expiration_time"):
        kalshi_market_to_contract_draft(raw)


def test_kalshi_adapter_is_offline_only():
    assert "requests" not in kalshi.__dict__
    assert "urllib" not in kalshi.__dict__
    assert "http" not in kalshi.__dict__
