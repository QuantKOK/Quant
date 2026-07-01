import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from app.predictions import main as predictions_main  # noqa: E402
from scout.predictions.ledger import (  # noqa: E402
    GENESIS_HASH,
    PredictionLedgerError,
    append_prediction,
    build_prediction_record,
    canonical_json,
    verify_ledger,
)


def prediction_draft(probability=0.35):
    return {
        "evidence_as_of": "2026-07-01T12:00:00+00:00",
        "model_version": "baseline-v0.1",
        "target": {
            "nct_id": "NCT01234567",
            "sponsor": "Example Biotech",
            "trial_title": "Example pivotal study",
            "phase": "Phase 3",
            "therapeutic_area": "Neurology",
            "primary_endpoint": "Change from baseline at Week 24",
            "estimated_completion_date": "2027-03",
        },
        "prediction": {
            "probability_of_success": probability,
            "outcome_definition": "Trial meets its prespecified primary endpoint",
            "forecast_horizon_date": "2027-06-30",
        },
        "explanation": {
            "risk_factors": ["Small prior-study sample"],
            "rationale": "Prior evidence is limited and the assumed effect size is ambitious.",
            "evidence_urls": ["https://clinicaltrials.gov/study/NCT01234567"],
        },
    }


def test_build_prediction_record_is_deterministic_with_fixed_metadata():
    first = build_prediction_record(
        prediction_draft(),
        created_at="2026-07-01T13:00:00+00:00",
        prediction_id="prediction-1",
    )
    second = build_prediction_record(
        prediction_draft(),
        created_at="2026-07-01T13:00:00+00:00",
        prediction_id="prediction-1",
    )

    assert first == second
    assert first["previous_hash"] == GENESIS_HASH
    assert len(first["record_hash"]) == 64
    assert first["prediction"]["probability_of_failure"] == 0.65


def test_append_two_predictions_builds_valid_hash_chain(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    first = append_prediction(
        str(ledger),
        prediction_draft(0.35),
        created_at="2026-07-01T13:00:00+00:00",
        prediction_id="prediction-1",
    )
    second = append_prediction(
        str(ledger),
        prediction_draft(0.4),
        created_at="2026-07-01T14:00:00+00:00",
        prediction_id="prediction-2",
    )

    result = verify_ledger(str(ledger))

    assert result["ok"] is True
    assert result["record_count"] == 2
    assert result["head_hash"] == second["record_hash"]
    assert second["previous_hash"] == first["record_hash"]
    assert result["last_created_at"] == "2026-07-01T14:00:00+00:00"


def test_verify_detects_tampered_record(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    append_prediction(
        str(ledger),
        prediction_draft(),
        created_at="2026-07-01T13:00:00+00:00",
        prediction_id="prediction-1",
    )
    record = json.loads(ledger.read_text(encoding="utf-8"))
    record["prediction"]["probability_of_success"] = 0.99
    ledger.write_text(canonical_json(record) + "\n", encoding="utf-8")

    result = verify_ledger(str(ledger))

    assert result["ok"] is False
    assert any("record_hash mismatch" in error for error in result["errors"])


def test_verify_rejects_rehashed_record_missing_required_fields(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    record = build_prediction_record(
        prediction_draft(),
        created_at="2026-07-01T13:00:00+00:00",
        prediction_id="prediction-1",
    )
    del record["explanation"]["rationale"]
    # Recompute the hash to prove schema verification is independent of tamper detection.
    from scout.predictions import ledger as ledger_module

    record["record_hash"] = ledger_module._record_hash(record)
    ledger.write_text(canonical_json(record) + "\n", encoding="utf-8")

    result = verify_ledger(str(ledger))

    assert result["ok"] is False
    assert any("explanation.rationale" in error for error in result["errors"])


def test_append_rejects_duplicate_prediction_id(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    append_prediction(
        str(ledger),
        prediction_draft(),
        created_at="2026-07-01T13:00:00+00:00",
        prediction_id="prediction-1",
    )

    with pytest.raises(PredictionLedgerError, match="duplicate prediction_id"):
        append_prediction(
            str(ledger),
            prediction_draft(),
            created_at="2026-07-01T14:00:00+00:00",
            prediction_id="prediction-1",
        )


def test_append_rejects_timestamp_earlier_than_ledger_head(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    append_prediction(
        str(ledger),
        prediction_draft(),
        created_at="2026-07-01T14:00:00+00:00",
        prediction_id="prediction-1",
    )

    with pytest.raises(PredictionLedgerError, match="earlier than the ledger head"):
        append_prediction(
            str(ledger),
            prediction_draft(),
            created_at="2026-07-01T13:00:00+00:00",
            prediction_id="prediction-2",
        )


@pytest.mark.parametrize("probability", [-0.1, 1.1, True, "0.5"])
def test_build_rejects_invalid_probability(probability):
    with pytest.raises(PredictionLedgerError, match="probability_of_success"):
        build_prediction_record(
            prediction_draft(probability),
            created_at="2026-07-01T13:00:00+00:00",
        )


def test_build_rejects_future_evidence():
    draft = prediction_draft()
    draft["evidence_as_of"] = "2026-07-02T12:00:00+00:00"

    with pytest.raises(PredictionLedgerError, match="evidence_as_of"):
        build_prediction_record(
            draft,
            created_at="2026-07-01T13:00:00+00:00",
        )


def test_build_rejects_invalid_nct_id():
    draft = prediction_draft()
    draft["target"]["nct_id"] = "ABC123"

    with pytest.raises(PredictionLedgerError, match="nct_id"):
        build_prediction_record(draft)


@pytest.mark.parametrize("field", ["risk_factors", "evidence_urls"])
def test_build_rejects_empty_explanation_lists(field):
    draft = prediction_draft()
    draft["explanation"][field] = []

    with pytest.raises(PredictionLedgerError, match=field):
        build_prediction_record(draft)


def test_build_rejects_non_http_evidence_url():
    draft = prediction_draft()
    draft["explanation"]["evidence_urls"] = ["file:///private/evidence.txt"]

    with pytest.raises(PredictionLedgerError, match="http or https"):
        build_prediction_record(draft)


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("prediction", "forecast_horizon_date"), "June 2027"),
        (("target", "estimated_completion_date"), "2027-99"),
    ],
)
def test_build_rejects_malformed_dates(field_path, value):
    draft = prediction_draft()
    draft[field_path[0]][field_path[1]] = value

    with pytest.raises(PredictionLedgerError, match=field_path[1]):
        build_prediction_record(draft)


def test_missing_ledger_verifies_as_empty(tmp_path):
    result = verify_ledger(str(tmp_path / "missing.jsonl"))

    assert result["ok"] is True
    assert result["record_count"] == 0
    assert result["head_hash"] == GENESIS_HASH


def test_cli_append_verify_and_head(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(prediction_draft()), encoding="utf-8")

    assert predictions_main(
        ["--ledger", str(ledger), "append", "--input", str(draft_path)]
    ) == 0
    append_output = json.loads(capsys.readouterr().out)
    assert append_output["record_hash"]

    assert predictions_main(["--ledger", str(ledger), "verify"]) == 0
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["ok"] is True
    assert verify_output["record_count"] == 1

    assert predictions_main(["--ledger", str(ledger), "head"]) == 0
    assert capsys.readouterr().out.strip() == verify_output["head_hash"]
