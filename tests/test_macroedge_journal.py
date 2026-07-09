import json
from pathlib import Path

import pytest

from macroedge.app import journal as journal_cli
from macroedge.journal import TradeJournalError, build_trade_candidate, canonical_json
from macroedge.ledger import (
    GENESIS_HASH,
    LedgerError,
    append_candidate,
    build_ledger_record,
    verify_ledger,
)


EXAMPLE = Path("macroedge/examples/trade-draft.example.json")
T1 = "2026-07-15T02:00:00+00:00"
T2 = "2026-07-16T02:00:00+00:00"


def _write_ledger(path, records):
    path.write_text("".join(canonical_json(r) + "\n" for r in records), encoding="utf-8")


def load_example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_build_trade_candidate_calculates_edge_and_hash():
    record = build_trade_candidate(
        load_example(),
        created_at="2026-07-15T02:00:00+00:00",
        candidate_id="candidate-1",
    )

    assert record["candidate_id"] == "candidate-1"
    assert record["market"]["implied_probability"] == 0.42
    assert record["thesis"]["fair_probability"] == 0.53
    assert record["thesis"]["edge"] == 0.11
    assert record["thesis"]["edge_percentage_points"] == 11.0
    assert record["contract_observation"]["observation_id"] == "example-contract-observation"
    assert len(record["candidate_hash"]) == 64


def test_build_trade_candidate_allows_manual_candidate_without_contract_observation():
    draft = load_example()
    draft.pop("contract_observation")

    record = build_trade_candidate(
        draft,
        created_at="2026-07-15T02:00:00+00:00",
        candidate_id="candidate-manual",
    )

    assert "contract_observation" not in record
    assert len(record["candidate_hash"]) == 64


def test_build_trade_candidate_rejects_bad_contract_hash():
    draft = load_example()
    draft["contract_observation"]["contract_hash"] = "not-a-hash"

    with pytest.raises(TradeJournalError, match="contract_hash"):
        build_trade_candidate(draft, created_at="2026-07-15T02:00:00+00:00")


def test_build_trade_candidate_rejects_weak_edge():
    draft = load_example()
    draft["thesis"]["fair_probability"] = 0.47

    with pytest.raises(TradeJournalError, match="edge"):
        build_trade_candidate(draft, created_at="2026-07-15T02:00:00+00:00")


def test_build_trade_candidate_rejects_trade_risk_above_cap():
    draft = load_example()
    draft["risk"]["planned_risk_usd"] = 30.0

    with pytest.raises(TradeJournalError, match="planned_risk"):
        build_trade_candidate(draft, created_at="2026-07-15T02:00:00+00:00")


def test_build_trade_candidate_rejects_event_exposure_above_cap():
    draft = load_example()
    draft["risk"]["current_event_exposure_usd"] = 40.0
    draft["risk"]["planned_risk_usd"] = 20.0

    with pytest.raises(TradeJournalError, match="max_event_exposure"):
        build_trade_candidate(draft, created_at="2026-07-15T02:00:00+00:00")


def test_build_trade_candidate_requires_http_sources():
    draft = load_example()
    draft["thesis"]["data_sources"] = ["not-a-url"]

    with pytest.raises(TradeJournalError, match="data_sources"):
        build_trade_candidate(draft, created_at="2026-07-15T02:00:00+00:00")


# --- Ledger: append + chaining ------------------------------------------------


def test_append_and_verify_roundtrip(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    r1 = append_candidate(str(ledger), load_example(), created_at=T1, candidate_id="c1")
    r2 = append_candidate(str(ledger), load_example(), created_at=T2, candidate_id="c2")

    assert r1["previous_hash"] == GENESIS_HASH
    assert r2["previous_hash"] == r1["ledger_hash"]  # predecessor chaining
    assert len(r1["ledger_hash"]) == 64

    result = verify_ledger(str(ledger))
    assert result["ok"] is True, result["errors"]
    assert result["record_count"] == 2
    assert result["head_hash"] == r2["ledger_hash"]


def test_append_rejects_weak_edge(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    draft = load_example()
    draft["thesis"]["fair_probability"] = 0.47  # edge 0.05 < 0.08 required

    with pytest.raises(TradeJournalError, match="edge"):
        append_candidate(str(ledger), draft, created_at=T1)
    assert not ledger.exists()  # nothing written on rejection


def test_append_rejects_duplicate_id_at_write_time(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    append_candidate(str(ledger), load_example(), created_at=T1, candidate_id="dup")
    with pytest.raises(LedgerError, match="duplicate candidate_id"):
        append_candidate(str(ledger), load_example(), created_at=T2, candidate_id="dup")


def test_append_rejects_backdated_created_at(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    append_candidate(str(ledger), load_example(), created_at=T2, candidate_id="c1")
    with pytest.raises(LedgerError, match="earlier than the ledger head"):
        append_candidate(str(ledger), load_example(), created_at=T1, candidate_id="c2")


# --- Ledger: verification / tamper detection ----------------------------------


def test_verify_detects_invalid_json(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("not json\n", encoding="utf-8")
    result = verify_ledger(str(ledger))
    assert result["ok"] is False
    assert any("invalid JSON" in e for e in result["errors"])


def test_verify_detects_edited_content(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    append_candidate(str(ledger), load_example(), created_at=T1, candidate_id="c1")
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    record["thesis"]["fair_probability"] = 0.90  # tamper without recomputing hashes
    ledger.write_text(canonical_json(record) + "\n", encoding="utf-8")

    result = verify_ledger(str(ledger))
    assert result["ok"] is False
    assert any("mismatch" in e for e in result["errors"])


def test_verify_detects_previous_hash_break(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    r1 = build_ledger_record(load_example(), GENESIS_HASH, created_at=T1, candidate_id="c1")
    # r2 wrongly points at GENESIS instead of r1's ledger_hash, but is self-consistent.
    r2 = build_ledger_record(load_example(), GENESIS_HASH, created_at=T2, candidate_id="c2")
    _write_ledger(ledger, [r1, r2])

    result = verify_ledger(str(ledger))
    assert result["ok"] is False
    assert any("previous_hash" in e for e in result["errors"])


def test_verify_detects_duplicate_candidate_id(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    r1 = build_ledger_record(load_example(), GENESIS_HASH, created_at=T1, candidate_id="dup")
    r2 = build_ledger_record(load_example(), r1["ledger_hash"], created_at=T2, candidate_id="dup")
    _write_ledger(ledger, [r1, r2])

    result = verify_ledger(str(ledger))
    assert result["ok"] is False
    assert any("duplicate candidate_id" in e for e in result["errors"])


def test_verify_detects_non_monotonic_created_at(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    r1 = build_ledger_record(load_example(), GENESIS_HASH, created_at=T2, candidate_id="c1")
    r2 = build_ledger_record(load_example(), r1["ledger_hash"], created_at=T1, candidate_id="c2")
    _write_ledger(ledger, [r1, r2])

    result = verify_ledger(str(ledger))
    assert result["ok"] is False
    assert any("non-monotonic created_at" in e for e in result["errors"])


def test_verify_detects_weak_edge_record(tmp_path):
    # A hand-crafted record with a real hash chain but a sub-threshold edge.
    weak = build_ledger_record(load_example(), GENESIS_HASH, created_at=T1, candidate_id="c1")
    weak["thesis"]["fair_probability"] = 0.45
    weak["thesis"]["edge"] = 0.03
    weak["ledger_hash"] = None  # force recompute below
    from macroedge.ledger import _ledger_hash

    weak["ledger_hash"] = _ledger_hash(weak)
    ledger = tmp_path / "ledger.jsonl"
    _write_ledger(ledger, [weak])

    result = verify_ledger(str(ledger))
    assert result["ok"] is False
    assert any("schema/risk/edge violation" in e or "mismatch" in e for e in result["errors"])


def test_append_to_invalid_ledger_raises(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("not json\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="invalid ledger"):
        append_candidate(str(ledger), load_example(), created_at=T1)


# --- CLI ----------------------------------------------------------------------


def test_cli_validate_append_verify_head(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"

    assert journal_cli.main(["validate", "--input", str(EXAMPLE), "--created-at", T2]) == 0
    assert '"ok": true' in capsys.readouterr().out

    assert journal_cli.main(
        ["append", "--input", str(EXAMPLE), "--ledger", str(ledger), "--created-at", T2, "--candidate-id", "cli-1"]
    ) == 0
    assert "ledger_hash" in capsys.readouterr().out

    assert journal_cli.main(["verify", "--ledger", str(ledger)]) == 0
    assert '"record_count": 1' in capsys.readouterr().out

    assert journal_cli.main(["head", "--ledger", str(ledger)]) == 0
    assert len(capsys.readouterr().out.strip()) == 64


def test_cli_verify_fails_on_tampered_ledger(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("not json\n", encoding="utf-8")
    assert journal_cli.main(["verify", "--ledger", str(ledger)]) == 1
