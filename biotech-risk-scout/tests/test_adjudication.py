import json
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from app import adjudicate as adjudicate_cli  # noqa: E402
from scout.adjudication.packets import (  # noqa: E402
    BatchError,
    apply_review_file,
    build_packet,
    build_packets,
    create_packets,
    make_packet_id,
    render_packets_jsonl,
)
from scout.adjudication.schema import (  # noqa: E402
    SCHEMA_VERSION,
    AdjudicationError,
    validate_evidence_source,
    validate_packet,
)
from scout.adjudication.verify import verify_batch  # noqa: E402
from scout.cohorts.freeze import (  # noqa: E402
    EXCLUSIONS_NAME,
    QUALITY_NAME,
    QUEUE_NAME,
    atomic_write_text,
    build_manifest,
    canonical_json,
    freeze_snapshot,
    sha256_bytes,
    sha256_text,
    write_manifest,
)

REVIEWED_AT = "2026-06-28T00:00:00+00:00"


def make_study(
    nct,
    *,
    primary_outcomes=None,
    posted_measures=None,
    results_first_posted="2022-03-01",
    has_results=True,
    overall_status="COMPLETED",
):
    if primary_outcomes is None:
        primary_outcomes = [
            {"measure": "Overall survival", "timeFrame": "24 months", "description": "OS vs control"}
        ]
    if posted_measures is None:
        posted_measures = [
            {"type": "PRIMARY", "title": "Overall survival"},
            {"type": "SECONDARY", "title": "Progression-free survival"},
        ]
    study = {
        "hasResults": has_results,
        "protocolSection": {
            "identificationModule": {"nctId": nct, "briefTitle": f"A Study of {nct}"},
            "statusModule": {
                "overallStatus": overall_status,
                "resultsFirstPostDateStruct": {"date": results_first_posted} if results_first_posted else {},
            },
            "outcomesModule": {"primaryOutcomes": primary_outcomes},
        },
    }
    if posted_measures is not None:
        study["resultsSection"] = {"outcomeMeasuresModule": {"outcomeMeasures": posted_measures}}
    return study


def make_authoritative(tmp_path, studies, *, cohort_version="test-cohort-v1"):
    """Write a complete authoritative cohort contract for packet tests."""
    auth = tmp_path / "authoritative"
    auth.mkdir()
    files = []
    for study in studies:
        nct = study["protocolSection"]["identificationModule"]["nctId"]
        relpath, sha = freeze_snapshot(str(auth), nct, study)
        files.append({"path": relpath, "sha256": sha})
    queue_text = ""
    quality_text = "{}\n"
    exclusions_text = ""
    atomic_write_text(str(auth / QUEUE_NAME), queue_text)
    atomic_write_text(str(auth / QUALITY_NAME), quality_text)
    atomic_write_text(str(auth / EXCLUSIONS_NAME), exclusions_text)
    nct_ids = sorted(
        study["protocolSection"]["identificationModule"]["nctId"]
        for study in studies
    )
    manifest = build_manifest(
        cohort_version=cohort_version,
        retrieval_timestamp=REVIEWED_AT,
        api_version={"apiVersion": "test", "dataTimestamp": REVIEWED_AT},
        data_timestamp=REVIEWED_AT,
        query={"test": True},
        inclusion_rules={"test": True},
        exclusion_reason_codes=[],
        included_nct_ids=nct_ids,
        excluded_counts_by_reason={},
        excluded_study_count=0,
        files=files,
        queue_sha256=sha256_text(queue_text),
        quality_report_sha256=sha256_text(quality_text),
        exclusions_sha256=sha256_text(exclusions_text),
    )
    write_manifest(str(auth), manifest)
    return str(auth)


def valid_proposal_packet(**overrides):
    packet = {
        "packet_id": make_packet_id("NCT00000001"),
        "nct_id": "NCT00000001",
        "source_snapshot_path": "raw/NCT00000001.json",
        "source_snapshot_sha256": "a" * 64,
        "primary_outcome_definitions": [{"measure": "OS", "time_frame": "24m", "description": ""}],
        "registry_results": {
            "has_posted_results": True,
            "results_first_posted_date": "2022-01-01",
            "primary_outcome_measure_titles": ["OS"],
            "primary_outcome_results": [
                {"type": "PRIMARY", "title": "OS", "analyses": []}
            ],
            "posted_outcome_measure_count": 2,
        },
        "evidence_sources": [
            {
                "source_type": "peer_reviewed_publication",
                "title": "Primary results of trial",
                "url": "https://example.org/article",
                "publication_date": "2023-02-01",
                "evidence_note": "Paraphrased: primary endpoint reported as met.",
            }
        ],
        "conflicts": [],
        "proposed_outcome": "met",
        "adjudication_rationale": "Primary endpoint mapped to the cited publication.",
        "confidence": "medium",
        "reviewer": "tester",
        "reviewed_at": REVIEWED_AT,
        "review_status": "needs_second_review",
        "historical_feature_snapshot_status": "unresolved",
        "schema_version": SCHEMA_VERSION,
    }
    packet.update(overrides)
    return packet


# --- Schema: evidence source validation ---------------------------------------


def test_evidence_source_accepts_valid():
    src = validate_evidence_source(
        {
            "source_type": "clinicaltrials_gov_results",
            "title": "Posted results",
            "url": "https://clinicaltrials.gov/study/NCT00000001",
            "publication_date": "2022-05-01",
            "evidence_note": "Registry posted results exist.",
        }
    )
    assert src["source_type"] == "clinicaltrials_gov_results"


def test_evidence_source_rejects_bad_url_and_date():
    with pytest.raises(AdjudicationError):
        validate_evidence_source(
            {"source_type": "sponsor_topline", "title": "t", "url": "ftp://x/y", "publication_date": "2022-01-01", "evidence_note": "n"}
        )
    with pytest.raises(AdjudicationError):
        validate_evidence_source(
            {"source_type": "sponsor_topline", "title": "t", "url": "https://x/y", "publication_date": "not-a-date", "evidence_note": "n"}
        )


def test_evidence_source_rejects_future_publication_date():
    reviewed = datetime.fromisoformat(REVIEWED_AT)
    with pytest.raises(AdjudicationError):
        validate_evidence_source(
            {
                "source_type": "regulatory_document",
                "title": "t",
                "url": "https://x/y",
                "publication_date": "2027-01-01",
                "evidence_note": "n",
            },
            reviewed_at=reviewed,
        )


# --- Schema: proposal rules ---------------------------------------------------


def test_null_proposal_with_confidence_rejected():
    with pytest.raises(AdjudicationError):
        validate_packet(valid_proposal_packet(proposed_outcome=None, confidence="high", review_status="unresolved"))


def test_null_proposal_cannot_be_needs_second_review():
    with pytest.raises(AdjudicationError):
        validate_packet(
            valid_proposal_packet(
                proposed_outcome=None, confidence=None, review_status="needs_second_review", evidence_sources=[]
            )
        )


def test_non_null_proposal_requires_needs_second_review():
    with pytest.raises(AdjudicationError):
        validate_packet(valid_proposal_packet(review_status="unresolved"))


def test_non_null_proposal_requires_evidence():
    with pytest.raises(AdjudicationError):
        validate_packet(valid_proposal_packet(evidence_sources=[]))


def test_non_null_proposal_requires_confidence():
    with pytest.raises(AdjudicationError):
        validate_packet(valid_proposal_packet(confidence=None))


def test_valid_non_null_proposal_accepted_and_conflicts_preserved():
    packet = validate_packet(
        valid_proposal_packet(
            conflicts=[{"description": "Publication and sponsor topline disagree on ORR", "source_urls": ["https://a/x", "https://b/y"]}]
        )
    )
    assert packet["proposed_outcome"] == "met"
    assert packet["review_status"] == "needs_second_review"
    assert len(packet["conflicts"]) == 1
    assert packet["conflicts"][0]["description"].startswith("Publication and sponsor")


def test_forbidden_field_rejected():
    with pytest.raises(AdjudicationError):
        validate_packet(valid_proposal_packet(**{"final_label": "met"}))


def test_historical_snapshot_status_must_be_unresolved():
    with pytest.raises(AdjudicationError):
        validate_packet(valid_proposal_packet(historical_feature_snapshot_status="resolved"))


# --- Packet building from frozen snapshots ------------------------------------


def test_build_packet_no_registry_status_inference():
    study = make_study("NCT00000001", overall_status="COMPLETED", has_results=True)
    packet = build_packet("NCT00000001", "raw/NCT00000001.json", "a" * 64, study, reviewer="t", reviewed_at=REVIEWED_AT)
    assert packet["proposed_outcome"] is None
    assert packet["confidence"] is None
    assert packet["review_status"] == "unresolved"
    assert packet["historical_feature_snapshot_status"] == "unresolved"


def test_build_packet_primary_and_coprimary_mapping():
    coprimary = [
        {"measure": "Overall survival", "timeFrame": "24m", "description": ""},
        {"measure": "Progression-free survival", "timeFrame": "12m", "description": ""},
    ]
    posted = [
        {"type": "PRIMARY", "title": "Overall survival"},
        {"type": "PRIMARY", "title": "Progression-free survival"},
        {"type": "SECONDARY", "title": "Response rate"},
    ]
    study = make_study("NCT00000002", primary_outcomes=coprimary, posted_measures=posted)
    packet = build_packet("NCT00000002", "raw/NCT00000002.json", "b" * 64, study, reviewer="t", reviewed_at=REVIEWED_AT)
    assert len(packet["primary_outcome_definitions"]) == 2
    # Secondary posted measure must NOT be captured as a primary result.
    assert packet["registry_results"]["primary_outcome_measure_titles"] == ["Overall survival", "Progression-free survival"]


def test_build_packets_verifies_snapshot_hash(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001"), make_study("NCT00000002")])
    packets, sha_map = build_packets(auth, ["NCT00000001", "NCT00000002"], reviewed_at=REVIEWED_AT)
    assert [p["nct_id"] for p in packets] == ["NCT00000001", "NCT00000002"]
    for packet in packets:
        assert packet["source_snapshot_sha256"] == sha_map[packet["source_snapshot_path"]]


def test_build_packets_detects_tampered_source(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001")])
    # Tamper the raw snapshot after the manifest recorded its hash.
    (tmp_path / "authoritative" / "raw" / "NCT00000001.json").write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(BatchError, match="tampered"):
        build_packets(auth, ["NCT00000001"], reviewed_at=REVIEWED_AT)


def test_build_packets_rejects_unknown_nct(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001")])
    with pytest.raises(BatchError, match="not present"):
        build_packets(auth, ["NCT09999999"], reviewed_at=REVIEWED_AT)


def test_build_packets_rejects_duplicate_request(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001")])
    with pytest.raises(BatchError, match="duplicate"):
        build_packets(auth, ["NCT00000001", "NCT00000001"], reviewed_at=REVIEWED_AT)


# --- create-packets: determinism, overwrite protection, verify ----------------


def test_create_and_verify_and_determinism(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000002"), make_study("NCT00000001")])
    out_a = tmp_path / "batch_a"
    out_b = tmp_path / "batch_b"
    s1 = create_packets(auth, str(out_a), nct_ids=["NCT00000001", "NCT00000002"], reviewed_at=REVIEWED_AT)
    s2 = create_packets(auth, str(out_b), nct_ids=["NCT00000001", "NCT00000002"], reviewed_at=REVIEWED_AT)

    assert (out_a / "packets.jsonl").read_bytes() == (out_b / "packets.jsonl").read_bytes()
    assert s1["manifest_sha256"] == s2["manifest_sha256"]

    result = verify_batch(str(out_a), auth)
    assert result["ok"] is True, result["errors"]
    assert result["packet_count"] == 2


def test_overwrite_protection_and_force(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001")])
    out = tmp_path / "batch"
    create_packets(auth, str(out), nct_ids=["NCT00000001"], reviewed_at=REVIEWED_AT)
    with pytest.raises(BatchError, match="refusing to overwrite"):
        create_packets(auth, str(out), nct_ids=["NCT00000001"], reviewed_at=REVIEWED_AT)
    # --force replaces transactionally.
    summary = create_packets(auth, str(out), nct_ids=["NCT00000001"], reviewed_at=REVIEWED_AT, force=True)
    assert summary["packet_count"] == 1
    assert verify_batch(str(out), auth)["ok"] is True


def test_apply_reviews_updates_only_review_fields_and_reseals(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001")])
    out = tmp_path / "batch"
    create_packets(
        auth,
        str(out),
        nct_ids=["NCT00000001"],
        reviewed_at=REVIEWED_AT,
    )
    proposal = valid_proposal_packet()
    review = {
        key: proposal[key]
        for key in (
            "nct_id",
            "evidence_sources",
            "conflicts",
            "proposed_outcome",
            "adjudication_rationale",
            "confidence",
            "reviewer",
            "reviewed_at",
            "review_status",
        )
    }
    reviews_path = tmp_path / "reviews.jsonl"
    reviews_path.write_text(json.dumps(review) + "\n", encoding="utf-8")

    result = apply_review_file(
        auth,
        str(out),
        str(reviews_path),
        force=True,
        generated_at=REVIEWED_AT,
    )

    assert result["reviewed_count"] == 1
    assert verify_batch(str(out), auth)["ok"] is True
    packet = json.loads((out / "packets.jsonl").read_text(encoding="utf-8"))
    assert packet["proposed_outcome"] == "met"
    assert packet["registry_results"]["primary_outcome_results"][0]["title"] == "Overall survival"


def test_apply_reviews_rejects_unknown_nct_and_requires_force(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001")])
    out = tmp_path / "batch"
    create_packets(
        auth,
        str(out),
        nct_ids=["NCT00000001"],
        reviewed_at=REVIEWED_AT,
    )
    review = {
        key: value
        for key, value in valid_proposal_packet(nct_id="NCT99999999").items()
        if key
        in {
            "nct_id",
            "evidence_sources",
            "conflicts",
            "proposed_outcome",
            "adjudication_rationale",
            "confidence",
            "reviewer",
            "reviewed_at",
            "review_status",
        }
    }
    reviews_path = tmp_path / "reviews.jsonl"
    reviews_path.write_text(json.dumps(review) + "\n", encoding="utf-8")

    with pytest.raises(BatchError, match="requires --force"):
        apply_review_file(auth, str(out), str(reviews_path))
    with pytest.raises(BatchError, match="outside this batch"):
        apply_review_file(auth, str(out), str(reviews_path), force=True)


def test_output_cannot_be_inside_authoritative(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001")])
    with pytest.raises(BatchError, match="authoritative"):
        create_packets(auth, os.path.join(auth, "sub"), nct_ids=["NCT00000001"], reviewed_at=REVIEWED_AT)


# --- verify: tamper / duplicate / drift detection -----------------------------


def _seed_batch(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001"), make_study("NCT00000002")])
    out = tmp_path / "batch"
    create_packets(auth, str(out), nct_ids=["NCT00000001", "NCT00000002"], reviewed_at=REVIEWED_AT)
    return auth, out


def test_verify_detects_edited_packet(tmp_path):
    auth, out = _seed_batch(tmp_path)
    packets_path = out / "packets.jsonl"
    text = packets_path.read_text(encoding="utf-8").replace("unresolved", "unresolved ", 1)
    packets_path.write_text(text, encoding="utf-8")
    result = verify_batch(str(out), auth)
    assert result["ok"] is False
    assert any("packets_sha256 mismatch" in e for e in result["errors"])


def test_verify_detects_edited_manifest(tmp_path):
    auth, out = _seed_batch(tmp_path)
    manifest_path = out / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["packet_count"] = 999
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = verify_batch(str(out), auth)
    assert result["ok"] is False
    assert any("manifest_sha256 mismatch" in e for e in result["errors"])


def test_verify_detects_reordered_packets(tmp_path):
    auth, out = _seed_batch(tmp_path)
    packets_path = out / "packets.jsonl"
    lines = packets_path.read_text(encoding="utf-8").splitlines()
    packets_path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    result = verify_batch(str(out), auth)
    assert result["ok"] is False


def test_verify_detects_authoritative_hash_mismatch(tmp_path):
    auth, out = _seed_batch(tmp_path)
    # Rewrite the authoritative manifest with a wrong hash for one snapshot.
    auth_manifest = os.path.join(auth, "manifest.json")
    data = json.loads(open(auth_manifest, encoding="utf-8").read())
    data["files"][0]["sha256"] = "f" * 64
    open(auth_manifest, "w", encoding="utf-8").write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    result = verify_batch(str(out), auth)
    assert result["ok"] is False
    assert any("authoritative manifest" in e for e in result["errors"])


def test_verify_rehashes_actual_authoritative_snapshot_bytes(tmp_path):
    auth, out = _seed_batch(tmp_path)
    snapshot = os.path.join(auth, "raw", "NCT00000001.json")
    with open(snapshot, "w", encoding="utf-8") as handle:
        handle.write('{"tampered":true}\n')

    result = verify_batch(str(out), auth)

    assert result["ok"] is False
    assert any("actual source snapshot bytes" in error for error in result["errors"])


# --- CLI ----------------------------------------------------------------------


def test_cli_create_verify_summary_export(tmp_path, capsys):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001"), make_study("NCT00000002")])
    out = str(tmp_path / "batch")

    rc = adjudicate_cli.main(
        ["create-packets", "--authoritative", auth, "--output", out,
         "--nct", "NCT00000001", "--nct", "NCT00000002", "--reviewed-at", REVIEWED_AT]
    )
    assert rc == 0
    capsys.readouterr()

    assert adjudicate_cli.main(["verify", "--output", out, "--authoritative", auth]) == 0
    assert '"ok": true' in capsys.readouterr().out

    assert adjudicate_cli.main(["summary", "--output", out]) == 0
    summary_out = capsys.readouterr().out
    assert "NCT00000001" in summary_out
    assert "not_final_notice" in summary_out
    assert "protocolSection" not in summary_out  # no raw payloads

    worksheet = str(tmp_path / "review.md")
    assert adjudicate_cli.main(["export-review-batch", "--output", out, "--out", worksheet]) == 0
    content = open(worksheet, encoding="utf-8").read()
    assert "Second-Review Worksheet" in content
    assert "NCT00000001" in content
    assert "Not final labels" in content


def test_cli_verify_fails_on_missing_batch(tmp_path):
    auth = make_authoritative(tmp_path, [make_study("NCT00000001")])
    rc = adjudicate_cli.main(["verify", "--output", str(tmp_path / "nope"), "--authoritative", auth])
    assert rc == 1
