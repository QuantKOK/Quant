import json
import os
from pathlib import Path

import pytest

from macroedge.app import contracts as contracts_cli
from macroedge.contracts import ContractError, build_contract_record, verify_contract_record


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


def test_verify_contract_record_accepts_emitted_record():
    record = build_contract_record(
        load_example(),
        observation_id="verify-contract",
        observed_at="2026-07-14T20:00:00-05:00",
    )

    result = verify_contract_record(record)

    assert result["ok"] is True
    assert result["contract_hash"] == record["contract_hash"]
    assert result["errors"] == []


def test_verify_contract_record_detects_tampering():
    record = build_contract_record(
        load_example(),
        observation_id="verify-contract",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    record["prices"]["yes_ask"] = 0.90

    result = verify_contract_record(record)

    assert result["ok"] is False
    assert any("mismatch" in error for error in result["errors"])


def test_verify_contract_record_preserves_non_default_status():
    draft = load_example()
    draft["market"]["status"] = "closed"
    record = build_contract_record(
        draft,
        observation_id="closed-contract",
        observed_at="2026-07-14T20:00:00-05:00",
    )

    result = verify_contract_record(record)

    assert record["status"] == "closed"
    assert result["ok"] is True, result["errors"]


def test_contract_cli_validate_and_emit(tmp_path, capsys):
    output = tmp_path / "contract-record.json"

    assert contracts_cli.main(
        [
            "validate",
            "--input",
            str(EXAMPLE),
            "--observation-id",
            "cli-contract",
            "--observed-at",
            "2026-07-14T20:00:00-05:00",
        ]
    ) == 0
    validate_output = capsys.readouterr().out
    assert '"ok": true' in validate_output
    assert '"contract_hash"' in validate_output

    assert contracts_cli.main(
        [
            "emit",
            "--input",
            str(EXAMPLE),
            "--output",
            str(output),
            "--observation-id",
            "cli-contract",
            "--observed-at",
            "2026-07-14T20:00:00-05:00",
        ]
    ) == 0
    emit_output = capsys.readouterr().out
    assert '"ok": true' in emit_output

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["observation_id"] == "cli-contract"
    assert len(record["contract_hash"]) == 64

    assert contracts_cli.main(["verify", "--input", str(output)]) == 0
    verify_output = capsys.readouterr().out
    assert '"ok": true' in verify_output
    assert record["contract_hash"] in verify_output


def test_contract_cli_validate_fails_on_bad_draft(tmp_path, capsys):
    bad = tmp_path / "bad-contract.json"
    draft = load_example()
    draft["prices"]["yes_bid"] = 0.70
    draft["prices"]["yes_ask"] = 0.60
    bad.write_text(json.dumps(draft), encoding="utf-8")

    assert contracts_cli.main(["validate", "--input", str(bad)]) == 1
    assert "validate failed" in capsys.readouterr().err


def test_contract_cli_emit_cleans_temp_file_on_replace_failure(tmp_path, capsys, monkeypatch):
    output = tmp_path / "contract-record.json"

    def fail_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    assert contracts_cli.main(
        [
            "emit",
            "--input",
            str(EXAMPLE),
            "--output",
            str(output),
            "--observation-id",
            "cli-contract",
            "--observed-at",
            "2026-07-14T20:00:00-05:00",
        ]
    ) == 1

    assert "emit failed" in capsys.readouterr().err
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_contract_cli_verify_fails_on_tampered_record(tmp_path, capsys):
    output = tmp_path / "contract-record.json"
    record = build_contract_record(
        load_example(),
        observation_id="cli-contract",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    record["prices"]["yes_bid"] = 0.01
    output.write_text(json.dumps(record), encoding="utf-8")

    assert contracts_cli.main(["verify", "--input", str(output)]) == 1
    assert '"ok": false' in capsys.readouterr().out
