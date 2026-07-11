import json
from pathlib import Path

import pytest

from macroedge.contracts import build_contract_record
from macroedge.app import journal as journal_cli
from macroedge.candidate_builder import (
    CandidateBuilderError,
    build_candidate_from_observation,
    build_trade_draft_from_observation,
)
from macroedge.journal import TradeJournalError, build_trade_candidate, canonical_json
from macroedge.ledger import (
    GENESIS_HASH,
    LedgerError,
    append_candidate,
    build_ledger_record,
    summarize_ledger,
    verify_ledger,
)
from macroedge.settlement_ledger import (
    SettlementLedgerError,
    append_settlement,
    summarize_ledger as summarize_settlement_ledger,
    verify_ledger as verify_settlement_ledger,
)
from macroedge.settlements import build_settlement_record, verify_settlement_record


EXAMPLE = Path("macroedge/examples/trade-draft.example.json")
CONTRACT_EXAMPLE = Path("macroedge/examples/contract-draft.example.json")
T1 = "2026-07-15T02:00:00+00:00"
T2 = "2026-07-16T02:00:00+00:00"


def _write_ledger(path, records):
    path.write_text("".join(canonical_json(r) + "\n" for r in records), encoding="utf-8")


def load_example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def load_contract_example():
    return json.loads(CONTRACT_EXAMPLE.read_text(encoding="utf-8"))


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


def test_build_trade_candidate_rejects_future_contract_observation():
    draft = load_example()
    draft["contract_observation"]["observed_at"] = "2026-07-16T02:00:00+00:00"

    with pytest.raises(TradeJournalError, match="observed_at"):
        build_trade_candidate(draft, created_at="2026-07-15T02:00:00+00:00")


def test_trade_example_reference_matches_built_contract():
    contract = build_contract_record(
        load_contract_example(),
        observation_id="example-contract-observation",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    draft = load_example()

    assert draft["contract_observation"]["contract_hash"] == contract["contract_hash"]


def test_end_to_end_contract_to_candidate_lineage():
    contract = build_contract_record(
        load_contract_example(),
        observation_id="linked-contract",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    draft = load_example()
    draft["contract_observation"] = {
        "observation_id": contract["observation_id"],
        "contract_hash": contract["contract_hash"],
        "observed_at": contract["observed_at"],
    }

    candidate = build_trade_candidate(
        draft,
        created_at="2026-07-15T02:00:00+00:00",
        candidate_id="linked-candidate",
    )

    assert candidate["contract_observation"]["observation_id"] == "linked-contract"
    assert candidate["contract_observation"]["contract_hash"] == contract["contract_hash"]


def test_build_trade_draft_from_observation_copies_lineage_and_market_facts():
    observation = build_contract_record(
        load_contract_example(),
        observation_id="obs-from-contract",
        observed_at="2026-07-14T20:00:00-05:00",
    )

    draft = build_trade_draft_from_observation(
        observation,
        side="YES",
        fair_probability=0.53,
        thesis_summary="Manual analyst thesis, not an automated recommendation.",
        data_sources=["https://www.bls.gov/cpi/"],
        active_bankroll_usd=400.0,
        planned_risk_usd=20.0,
    )
    candidate = build_trade_candidate(
        draft,
        created_at="2026-07-15T02:00:00+00:00",
        candidate_id="from-observation",
    )

    assert draft["contract_observation"]["observation_id"] == "obs-from-contract"
    assert draft["contract_observation"]["contract_hash"] == observation["contract_hash"]
    assert draft["market"]["entry_price"] == 0.42
    assert draft["thesis"]["evidence_as_of"] == observation["observed_at"]
    assert candidate["candidate_id"] == "from-observation"
    assert candidate["thesis"]["edge_percentage_points"] == 11.0


def test_build_trade_draft_from_observation_supports_no_side_complement():
    observation = build_contract_record(
        load_contract_example(),
        observation_id="obs-no-side",
        observed_at="2026-07-14T20:00:00-05:00",
    )

    candidate = build_candidate_from_observation(
        observation,
        side="NO",
        fair_probability=0.67,
        thesis_summary="Manual NO-side thesis.",
        data_sources=["https://www.bls.gov/cpi/"],
        active_bankroll_usd=400.0,
        planned_risk_usd=20.0,
        created_at="2026-07-15T02:00:00+00:00",
        candidate_id="no-side",
    )

    assert candidate["market"]["side"] == "NO"
    assert candidate["market"]["entry_price"] == 0.58
    assert candidate["thesis"]["edge"] == 0.09


def test_build_trade_draft_from_observation_rejects_tampered_observation():
    observation = build_contract_record(
        load_contract_example(),
        observation_id="tampered-observation",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    observation["prices"]["yes_ask"] = 0.90

    with pytest.raises(CandidateBuilderError, match="verification failed"):
        build_trade_draft_from_observation(
            observation,
            side="YES",
            fair_probability=0.53,
            thesis_summary="Manual thesis.",
            data_sources=["https://www.bls.gov/cpi/"],
            active_bankroll_usd=400.0,
            planned_risk_usd=20.0,
        )


def test_build_trade_candidate_rejects_non_iso_release_datetime():
    draft = load_example()
    draft["event"]["release_datetime"] = "not-a-date"

    with pytest.raises(TradeJournalError, match="release_datetime"):
        build_trade_candidate(draft, created_at="2026-07-15T02:00:00+00:00")


def test_build_trade_candidate_rejects_non_iso_settlement_datetime():
    draft = load_example()
    draft["event"]["settlement_datetime"] = "not-a-date"

    with pytest.raises(TradeJournalError, match="settlement_datetime"):
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


def test_summarize_candidate_ledger_reports_risk_edge_and_mix(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    first = load_example()
    second = load_example()
    second["event"]["event_type"] = "fed_decision"
    second["market"]["side"] = "NO"
    second["market"]["entry_price"] = 0.35
    second["thesis"]["fair_probability"] = 0.45
    second["risk"]["planned_risk_usd"] = 10.0
    second["post_mortem"]["outcome"] = "won"

    append_candidate(str(ledger), first, created_at=T1, candidate_id="summary-1")
    append_candidate(str(ledger), second, created_at=T2, candidate_id="summary-2")

    summary = summarize_ledger(str(ledger))

    assert summary["ok"] is True
    assert summary["record_count"] == 2
    assert summary["first_created_at"] == T1
    assert summary["last_created_at"] == T2
    assert summary["planned_risk_usd"] == 30.0
    assert summary["average_planned_risk_usd"] == 15.0
    assert summary["largest_planned_risk_usd"] == 20.0
    assert summary["average_edge_percentage_points"] == 10.5
    assert summary["largest_edge_percentage_points"] == 11.0
    assert summary["smallest_edge_percentage_points"] == 10.0
    assert summary["event_types"] == {"cpi": 1, "fed_decision": 1}
    assert summary["sides"] == {"NO": 1, "YES": 1}
    assert summary["statuses"] == {"candidate": 2}
    assert summary["open_post_mortems"] == 1
    assert summary["linked_contract_observations"] == 2


def test_summarize_candidate_ledger_handles_empty_and_invalid_ledgers(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty_summary = summarize_ledger(str(empty))
    assert empty_summary["ok"] is True
    assert empty_summary["record_count"] == 0
    assert empty_summary["average_edge_percentage_points"] is None

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text("not json\n", encoding="utf-8")
    invalid_summary = summarize_ledger(str(invalid))
    assert invalid_summary["ok"] is False
    assert invalid_summary["errors"]
    assert invalid_summary["event_types"] == {}


# --- Settlement / post-mortem ledger -----------------------------------------


def test_build_settlement_record_calculates_outcome_and_brier_score():
    candidate = build_trade_candidate(
        load_example(),
        created_at=T1,
        candidate_id="settlement-candidate",
    )

    settlement = build_settlement_record(
        candidate,
        actual_result="YES",
        settled_at="2026-07-15T12:00:00+00:00",
        recorded_at="2026-07-15T13:00:00+00:00",
        settlement_id="settlement-1",
        notes="Resolved from official source.",
        mistake_tags=["none"],
    )

    assert settlement["candidate"]["candidate_id"] == "settlement-candidate"
    assert settlement["settlement"]["outcome"] == "won"
    assert settlement["settlement"]["brier_score"] == 0.2209
    assert len(settlement["settlement_hash"]) == 64
    assert verify_settlement_record(settlement)["ok"] is True


def test_build_settlement_record_handles_lost_and_void_outcomes():
    candidate = build_trade_candidate(
        load_example(),
        created_at=T1,
        candidate_id="settlement-candidate",
    )

    lost = build_settlement_record(
        candidate,
        actual_result="NO",
        settled_at="2026-07-15T12:00:00+00:00",
        recorded_at="2026-07-15T13:00:00+00:00",
    )
    void = build_settlement_record(
        candidate,
        actual_result="VOID",
        settled_at="2026-07-15T12:00:00+00:00",
        recorded_at="2026-07-15T13:00:00+00:00",
    )

    assert lost["settlement"]["outcome"] == "lost"
    assert lost["settlement"]["brier_score"] == 0.2809
    assert void["settlement"]["outcome"] == "void"
    assert void["settlement"]["brier_score"] is None


def test_append_settlement_verify_and_summary_roundtrip(tmp_path):
    journal = tmp_path / "journal.jsonl"
    settlements = tmp_path / "settlements.jsonl"
    candidate = append_candidate(
        str(journal),
        load_example(),
        created_at=T1,
        candidate_id="settle-1",
    )

    record = append_settlement(
        str(settlements),
        candidate,
        actual_result="YES",
        settled_at="2026-07-15T12:00:00+00:00",
        recorded_at="2026-07-15T13:00:00+00:00",
        settlement_id="settlement-1",
    )

    verification = verify_settlement_ledger(str(settlements))
    assert verification["ok"] is True, verification["errors"]
    assert verification["record_count"] == 1
    assert verification["head_hash"] == record["ledger_hash"]

    summary = summarize_settlement_ledger(str(settlements))
    assert summary["ok"] is True
    assert summary["outcomes"] == {"won": 1}
    assert summary["actual_results"] == {"YES": 1}
    assert summary["event_types"] == {"cpi": 1}
    assert summary["average_brier_score"] == 0.2209


def test_append_settlement_rejects_duplicate_candidate(tmp_path):
    settlements = tmp_path / "settlements.jsonl"
    candidate = build_trade_candidate(
        load_example(),
        created_at=T1,
        candidate_id="settle-dup",
    )
    append_settlement(
        str(settlements),
        candidate,
        actual_result="YES",
        settled_at="2026-07-15T12:00:00+00:00",
        recorded_at="2026-07-15T13:00:00+00:00",
        settlement_id="settlement-1",
    )

    with pytest.raises(SettlementLedgerError, match="duplicate settlement"):
        append_settlement(
            str(settlements),
            candidate,
            actual_result="YES",
            settled_at="2026-07-15T12:00:00+00:00",
            recorded_at="2026-07-15T14:00:00+00:00",
            settlement_id="settlement-2",
        )


def test_verify_settlement_ledger_detects_tampering(tmp_path):
    settlements = tmp_path / "settlements.jsonl"
    candidate = build_trade_candidate(
        load_example(),
        created_at=T1,
        candidate_id="settle-tamper",
    )
    append_settlement(
        str(settlements),
        candidate,
        actual_result="YES",
        settled_at="2026-07-15T12:00:00+00:00",
        recorded_at="2026-07-15T13:00:00+00:00",
        settlement_id="settlement-1",
    )
    record = json.loads(settlements.read_text(encoding="utf-8").splitlines()[0])
    record["settlement"]["outcome"] = "lost"
    settlements.write_text(canonical_json(record) + "\n", encoding="utf-8")

    result = verify_settlement_ledger(str(settlements))

    assert result["ok"] is False
    assert any("mismatch" in error or "outcome" in error for error in result["errors"])


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

    assert journal_cli.main(["summary", "--ledger", str(ledger)]) == 0
    summary_output = capsys.readouterr().out
    assert '"planned_risk_usd": 20.0' in summary_output
    assert '"average_edge_percentage_points": 11.0' in summary_output
    assert '"YES": 1' in summary_output

    assert journal_cli.main(["head", "--ledger", str(ledger)]) == 0
    assert len(capsys.readouterr().out.strip()) == 64


def test_cli_draft_from_observation_writes_valid_candidate_draft(tmp_path, capsys):
    observation_path = tmp_path / "observation.json"
    output = tmp_path / "candidate-draft.json"
    observation = build_contract_record(
        load_contract_example(),
        observation_id="cli-observation",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    observation_path.write_text(json.dumps(observation), encoding="utf-8")

    assert journal_cli.main(
        [
            "draft-from-observation",
            "--input",
            str(observation_path),
            "--output",
            str(output),
            "--side",
            "YES",
            "--fair-probability",
            "0.53",
            "--thesis-summary",
            "Manual CPI thesis from cited sources.",
            "--data-source",
            "https://www.bls.gov/cpi/",
            "--active-bankroll-usd",
            "400",
            "--planned-risk-usd",
            "20",
            "--created-at",
            "2026-07-15T02:00:00+00:00",
            "--candidate-id",
            "cli-draft",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert '"edge_percentage_points": 11.0' in out

    draft = json.loads(output.read_text(encoding="utf-8"))
    assert draft["contract_observation"]["observation_id"] == "cli-observation"
    assert draft["market"]["entry_price"] == 0.42

    assert journal_cli.main(
        [
            "validate",
            "--input",
            str(output),
            "--created-at",
            "2026-07-15T02:00:00+00:00",
            "--candidate-id",
            "cli-draft",
        ]
    ) == 0


def test_cli_draft_from_observation_rejects_weak_edge_and_writes_nothing(tmp_path, capsys):
    observation_path = tmp_path / "observation.json"
    output = tmp_path / "candidate-draft.json"
    observation = build_contract_record(
        load_contract_example(),
        observation_id="cli-weak-edge",
        observed_at="2026-07-14T20:00:00-05:00",
    )
    observation_path.write_text(json.dumps(observation), encoding="utf-8")

    assert journal_cli.main(
        [
            "draft-from-observation",
            "--input",
            str(observation_path),
            "--output",
            str(output),
            "--side",
            "YES",
            "--fair-probability",
            "0.47",
            "--thesis-summary",
            "Weak edge thesis.",
            "--data-source",
            "https://www.bls.gov/cpi/",
            "--active-bankroll-usd",
            "400",
            "--planned-risk-usd",
            "20",
            "--created-at",
            "2026-07-15T02:00:00+00:00",
        ]
    ) == 1
    assert "edge" in capsys.readouterr().err
    assert not output.exists()


def test_cli_settle_verify_and_summary(tmp_path, capsys):
    journal = tmp_path / "journal.jsonl"
    settlements = tmp_path / "settlements.jsonl"
    assert journal_cli.main(
        [
            "append",
            "--input",
            str(EXAMPLE),
            "--ledger",
            str(journal),
            "--created-at",
            T1,
            "--candidate-id",
            "cli-settle",
        ]
    ) == 0
    capsys.readouterr()

    assert journal_cli.main(
        [
            "settle",
            "--journal-ledger",
            str(journal),
            "--settlement-ledger",
            str(settlements),
            "--candidate-id",
            "cli-settle",
            "--actual-result",
            "YES",
            "--settled-at",
            "2026-07-15T12:00:00+00:00",
            "--recorded-at",
            "2026-07-15T13:00:00+00:00",
            "--settlement-id",
            "cli-settlement",
            "--notes",
            "Resolved from official source.",
        ]
    ) == 0
    settle_output = capsys.readouterr().out
    assert '"outcome": "won"' in settle_output
    assert '"brier_score": 0.2209' in settle_output

    assert journal_cli.main(["verify-settlements", "--ledger", str(settlements)]) == 0
    assert '"record_count": 1' in capsys.readouterr().out

    assert journal_cli.main(["settlement-summary", "--ledger", str(settlements)]) == 0
    summary_output = capsys.readouterr().out
    assert '"won": 1' in summary_output
    assert '"average_brier_score": 0.2209' in summary_output


def test_cli_settle_reports_clean_error_on_bad_timestamps(tmp_path, capsys):
    journal = tmp_path / "journal.jsonl"
    settlements = tmp_path / "settlements.jsonl"
    assert journal_cli.main(
        [
            "append",
            "--input",
            str(EXAMPLE),
            "--ledger",
            str(journal),
            "--created-at",
            T1,
            "--candidate-id",
            "cli-bad-timestamps",
        ]
    ) == 0
    capsys.readouterr()

    assert journal_cli.main(
        [
            "settle",
            "--journal-ledger",
            str(journal),
            "--settlement-ledger",
            str(settlements),
            "--candidate-id",
            "cli-bad-timestamps",
            "--actual-result",
            "YES",
            "--settled-at",
            "2026-07-15T13:00:00+00:00",
            "--recorded-at",
            "2026-07-15T12:00:00+00:00",
        ]
    ) == 1
    err = capsys.readouterr().err
    assert "settle failed:" in err
    assert "recorded_at cannot be earlier than settled_at" in err
    assert not settlements.exists()


def test_cli_settle_rejects_duplicate_candidate(tmp_path, capsys):
    journal = tmp_path / "journal.jsonl"
    settlements = tmp_path / "settlements.jsonl"
    assert journal_cli.main(
        [
            "append",
            "--input",
            str(EXAMPLE),
            "--ledger",
            str(journal),
            "--created-at",
            T1,
            "--candidate-id",
            "cli-duplicate-settlement",
        ]
    ) == 0
    capsys.readouterr()
    settle_args = [
        "settle",
        "--journal-ledger",
        str(journal),
        "--settlement-ledger",
        str(settlements),
        "--candidate-id",
        "cli-duplicate-settlement",
        "--actual-result",
        "YES",
        "--settled-at",
        "2026-07-15T12:00:00+00:00",
        "--recorded-at",
        "2026-07-15T13:00:00+00:00",
    ]

    assert journal_cli.main([*settle_args, "--settlement-id", "cli-settlement-1"]) == 0
    capsys.readouterr()
    assert journal_cli.main([*settle_args, "--settlement-id", "cli-settlement-2"]) == 1
    assert "duplicate settlement" in capsys.readouterr().err


def test_settlement_modules_are_offline_only():
    banned = ("urllib", "requests", "http.client", "socket", "httpx", "aiohttp", "websocket")
    for path in (
        Path("macroedge/settlements.py"),
        Path("macroedge/settlement_ledger.py"),
    ):
        source = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in source, f"network import '{token}' must not appear in {path}"


def test_cli_verify_fails_on_tampered_ledger(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("not json\n", encoding="utf-8")
    assert journal_cli.main(["verify", "--ledger", str(ledger)]) == 1
