import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from app import outcomes as outcomes_cli  # noqa: E402
from scout.outcomes.dataset import (  # noqa: E402
    build_dataset,
    normalize_records,
    summarize_dataset,
    verify_dataset,
)
from scout.outcomes.schema import (  # noqa: E402
    SCHEMA_VERSION,
    OutcomeValidationError,
    validate_record,
)

EXAMPLE_SOURCE = os.path.join(PROJECT_ROOT, "data", "outcomes", "example_source.jsonl")
FIXED_TIME = "2026-01-01T00:00:00+00:00"


def valid_record(**overrides):
    base = {
        "record_id": "hor-t-0001",
        "nct_id": "NCT12345678",
        "sponsor": "Test Sponsor",
        "intervention": "TST-1",
        "indication": "Test Indication",
        "phase": "Phase 2",
        "prediction_cutoff_at": "2023-01-01T00:00:00+00:00",
        "outcome_observed_at": "2023-06-01T00:00:00+00:00",
        "registry_status": "COMPLETED",
        "scientific_outcome": "met",
        "outcome_definition": "Primary endpoint X at week 12.",
        "adjudication_rationale": "Endpoint met per synthetic readout.",
        "evidence_urls": ["https://example.com/evidence"],
        "source_publication_dates": ["2023-05-20"],
        "adjudicator": "test-adjudicator",
    }
    base.update(overrides)
    return base


# --- Deterministic build ------------------------------------------------------


def test_build_is_deterministic_and_counts_labels(tmp_path):
    out1 = tmp_path / "d1.jsonl"
    out2 = tmp_path / "d2.jsonl"
    m1 = build_dataset(EXAMPLE_SOURCE, str(out1), str(tmp_path / "m1.json"), generated_at=FIXED_TIME)
    m2 = build_dataset(EXAMPLE_SOURCE, str(out2), str(tmp_path / "m2.json"), generated_at=FIXED_TIME)

    assert out1.read_bytes() == out2.read_bytes()
    assert m1["dataset_sha256"] == m2["dataset_sha256"]
    assert m1["record_count"] == 5
    assert m1["label_counts"] == {"met": 1, "not_met": 2, "indeterminate": 1, "censored": 1}
    assert m1["registry_status_counts"] == {"COMPLETED": 3, "TERMINATED": 2}
    assert m1["schema_version"] == SCHEMA_VERSION


def test_build_sorts_deterministically_regardless_of_input_order(tmp_path):
    records = [
        valid_record(record_id="b", nct_id="NCT00000002"),
        valid_record(record_id="a", nct_id="NCT00000001"),
    ]
    src1 = tmp_path / "s1.jsonl"
    src2 = tmp_path / "s2.jsonl"
    src1.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    src2.write_text("\n".join(json.dumps(r) for r in reversed(records)) + "\n", encoding="utf-8")

    out1 = tmp_path / "o1.jsonl"
    out2 = tmp_path / "o2.jsonl"
    build_dataset(str(src1), str(out1), str(tmp_path / "m1.json"), generated_at=FIXED_TIME)
    build_dataset(str(src2), str(out2), str(tmp_path / "m2.json"), generated_at=FIXED_TIME)

    assert out1.read_bytes() == out2.read_bytes()


# --- Verification & tamper detection ------------------------------------------


def test_verify_matches_manifest(tmp_path):
    out = tmp_path / "d.jsonl"
    man = tmp_path / "d.manifest.json"
    build_dataset(EXAMPLE_SOURCE, str(out), str(man), generated_at=FIXED_TIME)

    result = verify_dataset(str(out), str(man))

    assert result.ok is True
    assert result.errors == []
    assert result.record_count == 5


def test_verify_detects_tampering(tmp_path):
    out = tmp_path / "d.jsonl"
    man = tmp_path / "d.manifest.json"
    build_dataset(EXAMPLE_SOURCE, str(out), str(man), generated_at=FIXED_TIME)

    # Flip an adjudicated outcome directly in the dataset file.
    tampered = out.read_text(encoding="utf-8").replace('"scientific_outcome":"met"', '"scientific_outcome":"not_met"', 1)
    out.write_text(tampered, encoding="utf-8", newline="")

    result = verify_dataset(str(out), str(man))

    assert result.ok is False
    assert result.computed_sha256 != result.manifest_sha256
    assert any("SHA-256 mismatch" in e for e in result.errors)


# --- Schema validation --------------------------------------------------------


def test_duplicate_record_id_rejected():
    records = [valid_record(record_id="dup"), valid_record(record_id="dup", nct_id="NCT99999999")]
    with pytest.raises(OutcomeValidationError, match="duplicate"):
        normalize_records(records)


def test_invalid_nct_rejected():
    with pytest.raises(OutcomeValidationError, match="nct_id"):
        validate_record(valid_record(nct_id="NCT123"))


def test_invalid_iso_timestamp_rejected():
    with pytest.raises(OutcomeValidationError, match="ISO-8601"):
        validate_record(valid_record(prediction_cutoff_at="not-a-timestamp"))


def test_timestamp_requires_timezone_and_normalizes_to_utc():
    with pytest.raises(OutcomeValidationError, match="timezone"):
        validate_record(valid_record(prediction_cutoff_at="2023-01-01T00:00:00"))

    record = validate_record(
        valid_record(
            prediction_cutoff_at="2023-01-01T05:00:00+05:00",
            outcome_observed_at="2023-06-01T03:00:00+03:00",
        )
    )
    assert record.prediction_cutoff_at == "2023-01-01T00:00:00+00:00"
    assert record.outcome_observed_at == "2023-06-01T00:00:00+00:00"


def test_outcome_observed_before_cutoff_rejected():
    with pytest.raises(OutcomeValidationError, match="outcome_observed_at cannot predate"):
        validate_record(valid_record(outcome_observed_at="2022-12-31T00:00:00+00:00"))


def test_evidence_publication_backdating_rejected():
    with pytest.raises(OutcomeValidationError, match="cannot predate"):
        validate_record(valid_record(source_publication_dates=["2022-01-01"]))


def test_evidence_publication_date_rejects_trailing_garbage():
    with pytest.raises(OutcomeValidationError, match="valid ISO-8601 date"):
        validate_record(valid_record(source_publication_dates=["2023-05-20garbage"]))


def test_every_evidence_url_requires_a_paired_publication_date():
    with pytest.raises(OutcomeValidationError, match="same length"):
        validate_record(
            valid_record(
                evidence_urls=[
                    "https://example.com/first",
                    "https://example.com/second",
                ],
                source_publication_dates=["2023-05-20"],
            )
        )


def test_evidence_pairs_remain_aligned_when_normalized():
    record = validate_record(
        valid_record(
            evidence_urls=[
                "https://example.com/z",
                "https://example.com/a",
            ],
            source_publication_dates=["2023-05-22", "2023-05-20"],
        )
    )
    assert record.evidence_urls == (
        "https://example.com/a",
        "https://example.com/z",
    )
    assert record.source_publication_dates == ("2023-05-20", "2023-05-22")


def test_met_requires_evidence_url():
    with pytest.raises(OutcomeValidationError, match="evidence URL"):
        validate_record(
            valid_record(
                scientific_outcome="met",
                evidence_urls=[],
                source_publication_dates=[],
            )
        )


def test_not_met_requires_evidence_url():
    with pytest.raises(OutcomeValidationError, match="evidence URL"):
        validate_record(
            valid_record(
                scientific_outcome="not_met",
                evidence_urls=[],
                source_publication_dates=[],
            )
        )


def test_non_http_evidence_url_rejected():
    with pytest.raises(OutcomeValidationError, match="HTTP"):
        validate_record(valid_record(evidence_urls=["ftp://example.com/x"]))


def test_unknown_field_rejected():
    record = valid_record()
    record["surprise_field"] = "x"
    with pytest.raises(OutcomeValidationError, match="unknown field"):
        validate_record(record)


def test_probability_field_rejected():
    record = valid_record()
    record["probability"] = 0.73
    with pytest.raises(OutcomeValidationError, match="forbidden"):
        validate_record(record)


def test_investment_recommendation_field_rejected():
    record = valid_record()
    record["recommendation"] = "buy"
    with pytest.raises(OutcomeValidationError, match="forbidden"):
        validate_record(record)


def test_schema_version_mismatch_rejected():
    with pytest.raises(OutcomeValidationError, match="schema_version"):
        validate_record(valid_record(schema_version="9.9.9"))


# --- Registry status vs scientific outcome separation -------------------------


def test_registry_status_not_derived_from_outcome():
    completed_not_met = validate_record(
        valid_record(registry_status="COMPLETED", scientific_outcome="not_met")
    )
    assert completed_not_met.registry_status == "COMPLETED"
    assert completed_not_met.scientific_outcome == "not_met"

    terminated_censored = validate_record(
        valid_record(
            registry_status="TERMINATED",
            scientific_outcome="censored",
            evidence_urls=[],
            source_publication_dates=[],
        )
    )
    assert terminated_censored.registry_status == "TERMINATED"
    assert terminated_censored.scientific_outcome == "censored"


def test_example_dataset_separates_status_and_outcome(tmp_path):
    out = tmp_path / "d.jsonl"
    build_dataset(EXAMPLE_SOURCE, str(out), str(tmp_path / "m.json"), generated_at=FIXED_TIME)
    by_id = {json.loads(line)["record_id"]: json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()}

    # COMPLETED but not_met: COMPLETED is not success.
    assert by_id["hor-example-0002"]["registry_status"] == "COMPLETED"
    assert by_id["hor-example-0002"]["scientific_outcome"] == "not_met"
    # TERMINATED but censored: TERMINATED is not failure.
    assert by_id["hor-example-0003"]["registry_status"] == "TERMINATED"
    assert by_id["hor-example-0003"]["scientific_outcome"] == "censored"


# --- Censored and indeterminate are accepted and distinct ---------------------


def test_censored_allows_empty_evidence():
    record = validate_record(
        valid_record(scientific_outcome="censored", evidence_urls=[], source_publication_dates=[])
    )
    assert record.scientific_outcome == "censored"
    assert record.evidence_urls == ()


def test_indeterminate_distinct_from_censored():
    indeterminate = validate_record(
        valid_record(scientific_outcome="indeterminate", evidence_urls=[], source_publication_dates=[])
    )
    censored = validate_record(
        valid_record(scientific_outcome="censored", evidence_urls=[], source_publication_dates=[])
    )
    assert indeterminate.scientific_outcome == "indeterminate"
    assert censored.scientific_outcome == "censored"
    assert indeterminate.scientific_outcome != censored.scientific_outcome


# --- CLI ----------------------------------------------------------------------


def test_cli_build_verify_summary(tmp_path, capsys):
    out = tmp_path / "d.jsonl"
    man = tmp_path / "d.manifest.json"

    assert outcomes_cli.main(
        ["build", "--source", EXAMPLE_SOURCE, "--out", str(out), "--manifest", str(man), "--generated-at", FIXED_TIME]
    ) == 0
    assert out.exists() and man.exists()
    capsys.readouterr()

    assert outcomes_cli.main(["verify", "--dataset", str(out), "--manifest", str(man)]) == 0
    assert "verified" in capsys.readouterr().out

    assert outcomes_cli.main(["summary", "--dataset", str(out)]) == 0
    summary_text = capsys.readouterr().out
    assert "not_met" in summary_text
    assert "COMPLETED" in summary_text
    assert "not investment advice" in summary_text.lower()


def test_cli_verify_fails_on_tamper(tmp_path, capsys):
    out = tmp_path / "d.jsonl"
    man = tmp_path / "d.manifest.json"
    outcomes_cli.main(["build", "--source", EXAMPLE_SOURCE, "--out", str(out), "--manifest", str(man), "--generated-at", FIXED_TIME])
    capsys.readouterr()

    out.write_text(out.read_text(encoding="utf-8") + '{"injected":true}\n', encoding="utf-8", newline="")

    assert outcomes_cli.main(["verify", "--dataset", str(out), "--manifest", str(man)]) == 1


def test_cli_build_rejects_invalid_source(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(valid_record(nct_id="BADID")) + "\n", encoding="utf-8")

    rc = outcomes_cli.main(
        ["build", "--source", str(bad), "--out", str(tmp_path / "o.jsonl"), "--manifest", str(tmp_path / "m.json")]
    )

    assert rc == 1
    assert "Build failed" in capsys.readouterr().err


def test_build_rejects_invalid_generated_at_and_path_collisions(tmp_path):
    out = tmp_path / "d.jsonl"
    with pytest.raises(OutcomeValidationError, match="generated_at"):
        build_dataset(EXAMPLE_SOURCE, str(out), str(tmp_path / "m.json"), generated_at="yesterday")

    with pytest.raises(OutcomeValidationError, match="must be different"):
        build_dataset(EXAMPLE_SOURCE, str(out), str(out), generated_at=FIXED_TIME)


def test_verify_rejects_incomplete_manifest(tmp_path):
    out = tmp_path / "d.jsonl"
    man = tmp_path / "d.manifest.json"
    manifest = build_dataset(EXAMPLE_SOURCE, str(out), str(man), generated_at=FIXED_TIME)
    man.write_text(
        json.dumps({"dataset_sha256": manifest["dataset_sha256"]}),
        encoding="utf-8",
    )

    result = verify_dataset(str(out), str(man))

    assert result.ok is False
    assert any("missing required field" in error for error in result.errors)


def test_verify_checks_registry_counts_and_canonical_bytes(tmp_path):
    out = tmp_path / "d.jsonl"
    man = tmp_path / "d.manifest.json"
    manifest = build_dataset(EXAMPLE_SOURCE, str(out), str(man), generated_at=FIXED_TIME)
    manifest["registry_status_counts"]["COMPLETED"] = 999
    man.write_text(json.dumps(manifest), encoding="utf-8")

    result = verify_dataset(str(out), str(man))
    assert result.ok is False
    assert any("registry_status_counts mismatch" in error for error in result.errors)

    manifest = build_dataset(EXAMPLE_SOURCE, str(out), str(man), generated_at=FIXED_TIME)
    out.write_text("\n" + out.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    manifest["dataset_sha256"] = __import__("hashlib").sha256(out.read_bytes()).hexdigest()
    man.write_text(json.dumps(manifest), encoding="utf-8")

    result = verify_dataset(str(out), str(man))
    assert result.ok is False
    assert any("not canonical JSONL" in error for error in result.errors)


def test_summarize_dataset_hash_matches_build(tmp_path):
    out = tmp_path / "d.jsonl"
    manifest = build_dataset(EXAMPLE_SOURCE, str(out), str(tmp_path / "m.json"), generated_at=FIXED_TIME)

    summary = summarize_dataset(str(out))

    assert summary["dataset_sha256"] == manifest["dataset_sha256"]
    assert summary["record_count"] == manifest["record_count"]
    assert summary["label_counts"] == manifest["label_counts"]
