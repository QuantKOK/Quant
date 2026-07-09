import json
from pathlib import Path

import pytest

from macroedge.contracts import ContractError, build_contract_record


EXAMPLE = Path("macroedge/examples/contract-draft.example.json")


def load_example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_build_contract_record_calculates_midpoint_spread_and_hash():
    record = build_contract_record(load_example(), observation_id="obs-1")

    assert record["observation_id"] == "obs-1"
    assert record["event"]["event_type"] == "cpi"
    assert record["prices"]["midpoint_probability"] == 0.42
    assert record["prices"]["spread"] == 0.04
    assert record["prices"]["implied_probability_source"] == "bid_ask_midpoint"
    assert len(record["contract_hash"]) == 64


def test_build_contract_record_allows_last_price_only():
    draft = load_example()
    draft["prices"] = {"last_price": 0.61}

    record = build_contract_record(draft, observation_id="obs-last")

    assert record["prices"]["midpoint_probability"] is None
    assert record["prices"]["spread"] is None
    assert record["prices"]["implied_probability_source"] == "last_price"


def test_build_contract_record_rejects_bid_above_ask():
    draft = load_example()
    draft["prices"]["yes_bid"] = 0.55
    draft["prices"]["yes_ask"] = 0.50

    with pytest.raises(ContractError, match="yes_bid"):
        build_contract_record(draft)


def test_build_contract_record_requires_a_price():
    draft = load_example()
    draft["prices"] = {}

    with pytest.raises(ContractError, match="prices"):
        build_contract_record(draft)


def test_build_contract_record_requires_timezone_observed_at():
    draft = load_example()
    draft["observed_at"] = "2026-07-14T20:00:00"

    with pytest.raises(ContractError, match="timezone"):
        build_contract_record(draft)


def test_build_contract_record_rejects_bad_market_url():
    draft = load_example()
    draft["market"]["market_url"] = "kalshi"

    with pytest.raises(ContractError, match="market_url"):
        build_contract_record(draft)


def test_build_contract_record_rejects_non_iso_release_datetime():
    draft = load_example()
    draft["event"]["release_datetime"] = "not-a-date"

    with pytest.raises(ContractError, match="release_datetime"):
        build_contract_record(draft)


def test_build_contract_record_rejects_non_iso_settlement_datetime():
    draft = load_example()
    draft["event"]["settlement_datetime"] = "not-a-date"

    with pytest.raises(ContractError, match="settlement_datetime"):
        build_contract_record(draft)
