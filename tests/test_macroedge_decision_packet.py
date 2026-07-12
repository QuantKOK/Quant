import json
from pathlib import Path

import pytest

from macroedge.app import decision_packet as packet_cli
from macroedge.candidate_builder import build_candidate_from_observation
from macroedge.contracts import build_contract_record
from macroedge.decision_packet import (
    DecisionPacketError,
    build_decision_packet,
    candidate_seed_from_packet,
    verify_decision_packet,
)

CONTRACT_EXAMPLE = Path("macroedge/examples/contract-draft.example.json")
OBSERVED_AT = "2026-07-14T20:00:00-05:00"
CREATED_AT = "2026-07-15T02:00:00+00:00"


def make_observation(observation_id="obs-decision"):
    return build_contract_record(
        json.loads(CONTRACT_EXAMPLE.read_text(encoding="utf-8")),
        observation_id=observation_id,
        observed_at=OBSERVED_AT,
    )


def build(**overrides):
    kwargs = dict(
        side="YES",
        fair_probability=0.53,
        thesis_summary="Manual CPI thesis from cited sources; not an automated recommendation.",
        data_sources=["https://www.bls.gov/cpi/"],
        intent="paper",
        decision="candidate_ok",
        risk_notes="Size tiny; single-event exposure well under cap.",
        disconfirming_evidence="Recent shelter disinflation could pull the print lower.",
        packet_id="packet-1",
        created_at=CREATED_AT,
    )
    kwargs.update(overrides)
    return build_decision_packet(make_observation(), **kwargs)


def write_json(tmp_path, payload, name="observation.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- core build / verify ------------------------------------------------------


def test_build_packet_computes_edge_and_hash():
    packet = build()
    assessment = packet["assessment"]

    assert packet["market"]["side"] == "YES"
    assert assessment["market_implied_probability"] == 0.42  # bid/ask midpoint
    assert assessment["gross_edge"] == 0.11
    assert assessment["edge_percentage_points"] == 11.0
    assert assessment["clears_edge_threshold"] is True
    assert "seed a trade candidate" in assessment["suggested_next_action"]
    assert len(packet["packet_hash"]) == 64
    assert verify_decision_packet(packet)["ok"] is True


def test_no_side_uses_complement_implied_probability():
    packet = build(side="NO", fair_probability=0.67, decision="observe_only")
    assessment = packet["assessment"]

    assert packet["market"]["side"] == "NO"
    assert assessment["market_implied_probability"] == 0.58  # 1 - 0.42
    assert assessment["gross_edge"] == 0.09
    assert verify_decision_packet(packet)["ok"] is True


def test_weak_edge_does_not_clear_threshold():
    packet = build(fair_probability=0.45)  # edge 0.03 < 0.08 default
    assessment = packet["assessment"]

    assert assessment["gross_edge"] == 0.03
    assert assessment["clears_edge_threshold"] is False
    assert "do not seed a candidate" in assessment["suggested_next_action"]
    assert verify_decision_packet(packet)["ok"] is True


def test_custom_min_edge_threshold_is_respected():
    packet = build(min_edge_required=0.15)  # edge 0.11 < 0.15
    assert packet["assessment"]["clears_edge_threshold"] is False


def test_packet_is_deterministic_with_fixed_id_and_created_at():
    assert build()["packet_hash"] == build()["packet_hash"]


def test_build_rejects_unverified_observation():
    observation = make_observation()
    observation["prices"]["yes_ask"] = 0.99  # breaks contract_hash

    with pytest.raises(DecisionPacketError, match="verification failed"):
        build_decision_packet(
            observation,
            side="YES",
            fair_probability=0.53,
            thesis_summary="x",
            data_sources=["https://www.bls.gov/cpi/"],
            intent="paper",
            decision="candidate_ok",
            risk_notes="y",
            disconfirming_evidence="z",
            created_at=CREATED_AT,
        )


def test_build_rejects_bad_enums_and_missing_discipline_fields():
    with pytest.raises(DecisionPacketError, match="decision"):
        build(decision="maybe")
    with pytest.raises(DecisionPacketError, match="intent"):
        build(intent="all_in")
    with pytest.raises(DecisionPacketError, match="disconfirming_evidence"):
        build(disconfirming_evidence="   ")


def test_build_rejects_future_evidence():
    with pytest.raises(DecisionPacketError, match="evidence_as_of"):
        build(evidence_as_of="2026-08-01T00:00:00+00:00")


# --- tamper detection ---------------------------------------------------------


def test_verify_detects_hash_tamper():
    packet = build()
    packet["thesis"]["summary"] = "Silently edited after the fact."

    result = verify_decision_packet(packet)
    assert result["ok"] is False
    assert any("packet_hash mismatch" in e for e in result["errors"])


def test_verify_detects_edge_math_inconsistency():
    packet = build()
    # Flip fair_probability but leave the assessment (and re-sign the hash) so the
    # only surviving signal is the recomputed edge/threshold check.
    packet["thesis"]["fair_probability"] = 0.20
    from macroedge.decision_packet import _record_hash  # noqa: PLC0415

    packet["packet_hash"] = _record_hash(packet)

    result = verify_decision_packet(packet)
    assert result["ok"] is False
    assert any("gross_edge is inconsistent" in e for e in result["errors"])


def test_verify_detects_clears_threshold_tamper():
    packet = build(fair_probability=0.45)  # clears False
    packet["assessment"]["clears_edge_threshold"] = True
    from macroedge.decision_packet import _record_hash  # noqa: PLC0415

    packet["packet_hash"] = _record_hash(packet)

    result = verify_decision_packet(packet)
    assert result["ok"] is False
    assert any("clears_edge_threshold" in e or "suggested_next_action" in e for e in result["errors"])


# --- packet -> candidate bridge ----------------------------------------------


def test_candidate_seed_from_packet_feeds_candidate_builder():
    packet = build()
    seed = candidate_seed_from_packet(packet)

    candidate = build_candidate_from_observation(
        make_observation(),
        active_bankroll_usd=400.0,
        planned_risk_usd=20.0,
        created_at=CREATED_AT,
        candidate_id="from-packet",
        **seed,
    )
    assert candidate["market"]["side"] == "YES"
    assert candidate["thesis"]["edge_percentage_points"] == 11.0


def test_candidate_seed_rejects_non_candidate_ok_packet():
    packet = build(decision="observe_only")
    with pytest.raises(DecisionPacketError, match="only candidate_ok"):
        candidate_seed_from_packet(packet)


# --- CLI ----------------------------------------------------------------------


def _cli_args(obs_path):
    return [
        "--input", str(obs_path),
        "--side", "YES",
        "--fair-probability", "0.53",
        "--thesis-summary", "Manual CPI thesis.",
        "--data-source", "https://www.bls.gov/cpi/",
        "--intent", "paper",
        "--decision", "candidate_ok",
        "--risk-notes", "Tiny size.",
        "--disconfirming-evidence", "Shelter disinflation risk.",
        "--packet-id", "cli-packet",
        "--created-at", CREATED_AT,
    ]


def test_cli_validate_happy(tmp_path, capsys):
    obs = write_json(tmp_path, make_observation())
    rc = packet_cli.main(["validate", *_cli_args(obs)])
    out = capsys.readouterr().out

    assert rc == 0
    assert '"clears_edge_threshold": true' in out
    assert '"edge_percentage_points": 11.0' in out


def test_cli_emit_then_verify_roundtrip(tmp_path, capsys):
    obs = write_json(tmp_path, make_observation())
    out1 = tmp_path / "packet1.json"
    out2 = tmp_path / "packet2.json"

    assert packet_cli.main(["emit", "--output", str(out1), *_cli_args(obs)]) == 0
    capsys.readouterr()
    assert packet_cli.main(["emit", "--output", str(out2), *_cli_args(obs)]) == 0
    capsys.readouterr()
    # Reproducible bytes with fixed packet-id/created-at.
    assert out1.read_bytes() == out2.read_bytes()

    record = json.loads(out1.read_text(encoding="utf-8"))
    assert verify_decision_packet(record)["ok"] is True

    assert packet_cli.main(["verify", "--input", str(out1)]) == 0
    assert '"ok": true' in capsys.readouterr().out


def test_cli_verify_detects_tamper(tmp_path, capsys):
    obs = write_json(tmp_path, make_observation())
    out = tmp_path / "packet.json"
    packet_cli.main(["emit", "--output", str(out), *_cli_args(obs)])
    capsys.readouterr()

    record = json.loads(out.read_text(encoding="utf-8"))
    record["thesis"]["risk_notes"] = "tampered"
    out.write_text(json.dumps(record), encoding="utf-8")

    rc = packet_cli.main(["verify", "--input", str(out)])
    out_text = capsys.readouterr().out
    assert rc == 1
    assert '"ok": false' in out_text
    assert "packet_hash mismatch" in out_text


def test_cli_validate_reports_clean_error_on_bad_decision(tmp_path, capsys):
    obs = write_json(tmp_path, make_observation())
    args = _cli_args(obs)
    # argparse rejects unknown --decision choices before our code, so exercise a
    # semantic failure our code owns: a future evidence timestamp.
    args += ["--evidence-as-of", "2026-08-01T00:00:00+00:00"]
    rc = packet_cli.main(["validate", *args])
    assert rc == 1
    assert "validate failed" in capsys.readouterr().err


# --- offline guarantee --------------------------------------------------------


def test_modules_are_offline_only():
    for path in ("macroedge/decision_packet.py", "macroedge/app/decision_packet.py"):
        source = Path(path).read_text(encoding="utf-8")
        for banned in ("urllib.request", "requests", "http.client", "socket", "httpx", "aiohttp", "websocket"):
            assert banned not in source, f"network import '{banned}' must not appear in {path}"
