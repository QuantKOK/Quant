import json
import os
from pathlib import Path

import pytest

from macroedge.app import contracts as contracts_cli
from macroedge.contracts import ContractError, build_contract_record, verify_contract_record
from macroedge.contract_ledger import (
    GENESIS_HASH,
    ContractLedgerError,
    append_observation,
    build_ledger_record,
    summarize_ledger,
    verify_ledger,
)
from macroedge.journal import canonical_json


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


def test_append_observation_and_verify_ledger_roundtrip(tmp_path):
    ledger = tmp_path / "observations.jsonl"
    r1 = append_observation(
        str(ledger),
        load_example(),
        observation_id="obs-1",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    r2 = append_observation(
        str(ledger),
        load_example(),
        observation_id="obs-2",
        observed_at="2026-07-14T21:00:00-05:00",
    )

    assert r1["previous_hash"] == GENESIS_HASH
    assert r2["previous_hash"] == r1["ledger_hash"]

    result = verify_ledger(str(ledger))
    assert result["ok"] is True, result["errors"]
    assert result["record_count"] == 2
    assert result["head_hash"] == r2["ledger_hash"]


def test_summarize_observation_ledger_counts_current_tape(tmp_path):
    ledger = tmp_path / "observations.jsonl"
    append_observation(
        str(ledger),
        load_example(),
        observation_id="obs-cpi",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    draft = load_example()
    draft["event"]["event_type"] = "fed_decision"
    draft["market"]["status"] = "closed"
    draft["prices"] = {"last_price": 0.34}
    append_observation(
        str(ledger),
        draft,
        observation_id="obs-fed",
        observed_at="2026-07-14T21:00:00-05:00",
    )

    summary = summarize_ledger(str(ledger))

    assert summary["ok"] is True
    assert summary["record_count"] == 2
    assert summary["first_observed_at"] == "2026-07-14T20:00:00-05:00"
    assert summary["last_observed_at"] == "2026-07-14T21:00:00-05:00"
    assert summary["event_types"] == {"cpi": 1, "fed_decision": 1}
    assert summary["platforms"] == {"Kalshi": 2}
    assert summary["statuses"] == {"closed": 1, "observed": 1}
    assert summary["implied_probability_sources"] == {
        "bid_ask_midpoint": 1,
        "last_price": 1,
    }


def test_summarize_observation_ledger_reports_invalid_ledger(tmp_path):
    ledger = tmp_path / "observations.jsonl"
    ledger.write_text("{ not json\n", encoding="utf-8")

    summary = summarize_ledger(str(ledger))

    assert summary["ok"] is False
    assert summary["record_count"] == 0
    assert summary["errors"]
    assert summary["event_types"] == {}


def test_append_observation_rejects_duplicate_id(tmp_path):
    ledger = tmp_path / "observations.jsonl"
    append_observation(
        str(ledger),
        load_example(),
        observation_id="dup",
        observed_at="2026-07-14T20:00:00-05:00",
    )

    with pytest.raises(ContractLedgerError, match="duplicate observation_id"):
        append_observation(
            str(ledger),
            load_example(),
            observation_id="dup",
            observed_at="2026-07-14T21:00:00-05:00",
        )


def test_append_observation_rejects_backdated_observation(tmp_path):
    ledger = tmp_path / "observations.jsonl"
    append_observation(
        str(ledger),
        load_example(),
        observation_id="obs-1",
        observed_at="2026-07-14T21:00:00-05:00",
    )

    with pytest.raises(ContractLedgerError, match="earlier than the ledger head"):
        append_observation(
            str(ledger),
            load_example(),
            observation_id="obs-2",
            observed_at="2026-07-14T20:00:00-05:00",
        )


def test_verify_observation_ledger_detects_tampering(tmp_path):
    ledger = tmp_path / "observations.jsonl"
    append_observation(
        str(ledger),
        load_example(),
        observation_id="obs-1",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    record["prices"]["yes_ask"] = 0.90
    ledger.write_text(canonical_json(record) + "\n", encoding="utf-8")

    result = verify_ledger(str(ledger))

    assert result["ok"] is False
    assert any("mismatch" in error for error in result["errors"])


def test_verify_observation_ledger_detects_chain_break(tmp_path):
    ledger = tmp_path / "observations.jsonl"
    r1 = build_ledger_record(
        load_example(),
        GENESIS_HASH,
        observation_id="obs-1",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    r2 = build_ledger_record(
        load_example(),
        GENESIS_HASH,
        observation_id="obs-2",
        observed_at="2026-07-14T21:00:00-05:00",
    )
    ledger.write_text(canonical_json(r1) + "\n" + canonical_json(r2) + "\n", encoding="utf-8")

    result = verify_ledger(str(ledger))

    assert result["ok"] is False
    assert any("previous_hash" in error for error in result["errors"])


def test_contract_cli_append_verify_ledger_and_head(tmp_path, capsys):
    ledger = tmp_path / "observations.jsonl"

    assert contracts_cli.main(
        [
            "append",
            "--input",
            str(EXAMPLE),
            "--ledger",
            str(ledger),
            "--observation-id",
            "cli-ledger-obs",
            "--observed-at",
            "2026-07-14T20:00:00-05:00",
        ]
    ) == 0
    assert "ledger_hash" in capsys.readouterr().out

    assert contracts_cli.main(["verify-ledger", "--ledger", str(ledger)]) == 0
    verify_output = capsys.readouterr().out
    assert '"record_count": 1' in verify_output

    assert contracts_cli.main(["summary", "--ledger", str(ledger)]) == 0
    summary_output = capsys.readouterr().out
    assert '"event_types"' in summary_output
    assert '"cpi": 1' in summary_output

    assert contracts_cli.main(["head", "--ledger", str(ledger)]) == 0
    assert len(capsys.readouterr().out.strip()) == 64
