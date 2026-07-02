import json
import os
import sys
import urllib.parse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from app import cohort as cohort_cli  # noqa: E402
from scout.cohorts import clinicaltrials  # noqa: E402
from scout.cohorts.clinicaltrials import (  # noqa: E402
    STUDIES_URL,
    VERSION_URL,
    ClinicalTrialsClient,
    ClinicalTrialsNetworkError,
    ClinicalTrialsResponseError,
    MissingUserAgentError,
    TransientTransportError,
    resolve_user_agent,
)
from scout.cohorts.discovery import (  # noqa: E402
    CohortError,
    discover_cohort,
    rebuild_queue,
    verify_cohort,
)
from scout.cohorts.freeze import (
    EXCLUSIONS_NAME,
    QUALITY_NAME,
    QUEUE_NAME,
    RAW_DIR,
    load_manifest,
    manifest_hash,
)
from scout.cohorts.protocol import evaluate_study  # noqa: E402

FIXED_TIME = "2026-06-28T00:00:00+00:00"
VERSION_PAYLOAD = {"apiVersion": "2.0.3", "dataTimestamp": "2026-06-20T09:00:00Z"}


def make_study(
    nct,
    *,
    study_type="INTERVENTIONAL",
    phases=("PHASE3",),
    allocation="RANDOMIZED",
    sponsor_class="INDUSTRY",
    sponsor="Test Pharma Inc",
    intervention_types=("DRUG",),
    has_results=True,
    results_first_posted="2023-06-01",
    primary_completion="2022-05-01",
    enrollment=200,
    conditions=("Condition A",),
    primary_outcomes=None,
    title=None,
    overall_status="COMPLETED",
):
    interventions = [{"name": f"{t.title()} Agent", "type": t} for t in intervention_types]
    if primary_outcomes is None:
        primary_outcomes = [
            {"measure": "Overall survival", "timeFrame": "24 months", "description": "OS vs control"}
        ]
    status_mod = {"overallStatus": overall_status}
    if primary_completion:
        status_mod["primaryCompletionDateStruct"] = {"date": primary_completion}
    if results_first_posted:
        status_mod["resultsFirstPostDateStruct"] = {"date": results_first_posted}
    design = {
        "studyType": study_type,
        "phases": list(phases),
        "designInfo": {"allocation": allocation},
    }
    if enrollment is not None:
        design["enrollmentInfo"] = {"count": enrollment}
    return {
        "hasResults": has_results,
        "protocolSection": {
            "identificationModule": {"nctId": nct, "briefTitle": title or f"A Study of {nct}"},
            "statusModule": status_mod,
            "designModule": design,
            "armsInterventionsModule": {"interventions": interventions},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": sponsor_class}},
            "conditionsModule": {"conditions": list(conditions)},
            "outcomesModule": {"primaryOutcomes": primary_outcomes},
        },
    }


def _json_bytes(obj):
    return json.dumps(obj).encode("utf-8")


def _query_param(url, key):
    query = urllib.parse.urlparse(url).query
    values = urllib.parse.parse_qs(query).get(key)
    return values[0] if values else None


class FakeTransport:
    """Routes version/search/individual API calls from in-memory fixtures."""

    def __init__(self, *, pages, individual=None, version=None, transient_before=0, force_status=None, malformed=False):
        self.pages = pages  # list of (studies, next_token or None)
        self.individual = individual or {}
        self.version = version or VERSION_PAYLOAD
        self.transient_before = transient_before
        self.force_status = force_status
        self.malformed = malformed
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if self.transient_before > 0:
            self.transient_before -= 1
            raise TransientTransportError("simulated timeout")
        if self.force_status is not None:
            return self.force_status, b"{}"
        if url.startswith(VERSION_URL):
            if self.malformed:
                return 200, b"not-json"
            return 200, _json_bytes(self.version)
        rest = url[len(STUDIES_URL):]
        if rest.startswith("/"):
            nct = rest[1:].split("?")[0]
            study = self.individual.get(nct)
            if study is None:
                return 404, b"{}"
            return 200, _json_bytes(study)
        token = _query_param(url, "pageToken")
        index = 0 if token is None else int(token)
        studies, next_token = self.pages[index]
        payload = {"studies": studies, "totalCount": sum(len(p[0]) for p in self.pages)}
        if next_token is not None:
            payload["nextPageToken"] = next_token
        return 200, _json_bytes(payload)


def _client(transport, **kwargs):
    return ClinicalTrialsClient(user_agent="test-agent", transport=transport, sleep=lambda _s: None, **kwargs)


def _cohort_fixture(studies):
    """Return a transport serving all studies on one page + individual lookups."""
    individual = {s["protocolSection"]["identificationModule"]["nctId"]: s for s in studies}
    return FakeTransport(pages=[(studies, None)], individual=individual)


# --- Client: pagination, retries, malformed, user-agent -----------------------


def test_iter_studies_paginates():
    s1, s2, s3 = make_study("NCT00000001"), make_study("NCT00000002"), make_study("NCT00000003")
    transport = FakeTransport(pages=[([s1, s2], "1"), ([s3], None)])
    client = _client(transport)

    ncts = [s["protocolSection"]["identificationModule"]["nctId"] for s in client.iter_studies({"pageSize": 2})]

    assert ncts == ["NCT00000001", "NCT00000002", "NCT00000003"]


def test_unbounded_search_rejects_silent_hard_limit_truncation(monkeypatch):
    monkeypatch.setattr(clinicaltrials, "MAX_PAGES", 1)
    transport = FakeTransport(
        pages=[
            ([make_study("NCT00000001")], "1"),
            ([make_study("NCT00000002")], None),
        ]
    )

    with pytest.raises(ClinicalTrialsResponseError, match="hard pagination limit"):
        list(_client(transport).iter_studies({"pageSize": 1}))


def test_missing_user_agent_raises(monkeypatch):
    monkeypatch.delenv("CTG_USER_AGENT", raising=False)
    with pytest.raises(MissingUserAgentError):
        ClinicalTrialsClient()
    with pytest.raises(MissingUserAgentError):
        resolve_user_agent("   ")


def test_retry_then_success():
    transport = FakeTransport(pages=[([], None)], transient_before=1)
    client = _client(transport, max_retries=3)

    version = client.get_version()

    assert version["dataTimestamp"] == VERSION_PAYLOAD["dataTimestamp"]
    assert len(transport.calls) == 2  # one transient failure, one success


def test_retry_exhausted_raises_network_error():
    transport = FakeTransport(pages=[([], None)], force_status=503)
    client = _client(transport, max_retries=3)

    with pytest.raises(ClinicalTrialsNetworkError):
        client.get_version()
    assert len(transport.calls) == 3


def test_malformed_response_raises():
    transport = FakeTransport(pages=[([], None)], malformed=True)
    client = _client(transport)

    with pytest.raises(ClinicalTrialsResponseError):
        client.get_version()


# --- Eligibility: every exclusion reason --------------------------------------


def test_eligible_study_has_no_reasons():
    result = evaluate_study(make_study("NCT00000010"))
    assert result["eligible"] is True
    assert result["exclusion_reasons"] == []


def test_each_exclusion_reason():
    cases = {
        "not_interventional": make_study("NCT1", study_type="OBSERVATIONAL"),
        "not_phase3": make_study("NCT2", phases=("PHASE2",)),
        "no_drug_or_biological_intervention": make_study("NCT3", intervention_types=("DEVICE",)),
        "not_randomized": make_study("NCT4", allocation="NON_RANDOMIZED"),
        "not_industry_sponsored": make_study("NCT5", sponsor_class="OTHER"),
        "no_posted_results": make_study("NCT6", has_results=False, results_first_posted=None),
        "primary_completion_missing": make_study("NCT7", primary_completion=None),
        "primary_completion_out_of_range": make_study("NCT8", primary_completion="2019-06-01"),
        "enrollment_missing": make_study("NCT9", enrollment=None),
        "enrollment_below_minimum": make_study("NCT10", enrollment=10),
    }
    for reason, study in cases.items():
        result = evaluate_study(study)
        assert reason in result["exclusion_reasons"], reason
        assert result["eligible"] is False


def test_multiple_exclusion_reasons_recorded():
    study = make_study("NCT11", phases=("PHASE2",), enrollment=5)
    reasons = evaluate_study(study)["exclusion_reasons"]
    assert "not_phase3" in reasons
    assert "enrollment_below_minimum" in reasons


def test_malformed_completion_date_is_not_silently_truncated():
    result = evaluate_study(
        make_study("NCT00000011", primary_completion="2022-05-01garbage")
    )
    assert "primary_completion_missing" in result["exclusion_reasons"]


# --- Discovery: deterministic selection, cap, freezing ------------------------


def test_discovery_selects_first_n_by_nct(tmp_path):
    studies = [
        make_study("NCT00000001"),
        make_study("NCT00000002", phases=("PHASE2",)),  # excluded
        make_study("NCT00000003"),
        make_study("NCT00000004"),
    ]
    client = _client(_cohort_fixture(studies))
    out = tmp_path / "cohort"

    summary = discover_cohort(client, str(out), max_studies=2, retrieval_timestamp=FIXED_TIME)

    assert summary["included_nct_ids"] == ["NCT00000001", "NCT00000003"]
    assert summary["candidate_count"] == 2
    assert (out / RAW_DIR / "NCT00000001.json").exists()
    assert (out / RAW_DIR / "NCT00000003.json").exists()


def test_discovery_cap_enforced_hard_bound(tmp_path):
    client = _client(_cohort_fixture([make_study("NCT00000001")]))
    with pytest.raises(CohortError):
        discover_cohort(client, str(tmp_path / "c"), max_studies=101, retrieval_timestamp=FIXED_TIME)


def test_discovery_records_exclusions(tmp_path):
    studies = [make_study("NCT00000001"), make_study("NCT00000002", allocation="NON_RANDOMIZED")]
    client = _client(_cohort_fixture(studies))
    out = tmp_path / "cohort"

    summary = discover_cohort(client, str(out), max_studies=50, retrieval_timestamp=FIXED_TIME)

    assert summary["excluded_counts_by_reason"].get("not_randomized") == 1
    manifest = load_manifest(str(out))
    assert manifest["excluded_counts_by_reason"]["not_randomized"] == 1


def test_bounded_scan_is_recorded_and_flagged_high_risk(tmp_path):
    first = make_study("NCT00000001")
    second = make_study("NCT00000002")
    transport = FakeTransport(
        pages=[([first], "1"), ([second], None)],
        individual={"NCT00000001": first, "NCT00000002": second},
    )
    out = tmp_path / "cohort"

    summary = discover_cohort(
        _client(transport),
        str(out),
        max_studies=1,
        scan_page_limit=1,
        retrieval_timestamp=FIXED_TIME,
    )
    manifest = load_manifest(str(out))
    quality = json.loads((out / QUALITY_NAME).read_text(encoding="utf-8"))

    assert summary["selection_scope"] == "bounded_scan_window"
    assert manifest["query"]["scan_page_limit"] == 1
    assert quality["bounded_scan"] is True
    assert any(
        finding["code"] == "bounded_candidate_scan"
        and finding["severity"] == "high"
        for finding in quality["findings"]
    )


def test_full_study_is_revalidated_and_ineligible_result_is_backfilled(tmp_path):
    search_first = make_study("NCT00000001")
    search_second = make_study("NCT00000002")
    changed_first = make_study("NCT00000001", phases=("PHASE2",))
    transport = FakeTransport(
        pages=[([search_first, search_second], None)],
        individual={
            "NCT00000001": changed_first,
            "NCT00000002": search_second,
        },
    )

    summary = discover_cohort(
        _client(transport),
        str(tmp_path / "cohort"),
        max_studies=1,
        retrieval_timestamp=FIXED_TIME,
    )

    assert summary["included_nct_ids"] == ["NCT00000002"]
    assert summary["excluded_counts_by_reason"]["not_phase3"] == 1


# --- Freezing, manifest, verification, tamper detection -----------------------


def _discover(tmp_path, studies, **kwargs):
    client = _client(_cohort_fixture(studies))
    out = tmp_path / "cohort"
    discover_cohort(client, str(out), retrieval_timestamp=FIXED_TIME, **kwargs)
    return out


def test_snapshot_hashes_and_manifest_verify(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001"), make_study("NCT00000002")])
    result = verify_cohort(str(out))
    assert result["ok"] is True, result["errors"]


def test_manifest_edit_detected(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001")])
    manifest_path = out / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["candidate_count"] = 999
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_cohort(str(out))
    assert result["ok"] is False
    assert any("manifest_sha256 mismatch" in e for e in result["errors"])


def test_snapshot_edit_detected(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001")])
    snap = out / RAW_DIR / "NCT00000001.json"
    snap.write_text(snap.read_text(encoding="utf-8").replace("Test Pharma Inc", "Edited Sponsor"), encoding="utf-8")

    result = verify_cohort(str(out))
    assert result["ok"] is False
    assert any("hash mismatch" in e for e in result["errors"])


def test_missing_snapshot_detected(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001"), make_study("NCT00000002")])
    (out / RAW_DIR / "NCT00000002.json").unlink()

    result = verify_cohort(str(out))
    assert result["ok"] is False
    assert any("missing snapshot" in e for e in result["errors"])


def test_extra_snapshot_detected(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001")])
    (out / RAW_DIR / "NCT09999999.json").write_text("{}", encoding="utf-8")

    result = verify_cohort(str(out))
    assert result["ok"] is False
    assert any("extra snapshot" in e for e in result["errors"])


def test_manifest_snapshot_path_cannot_escape_cohort(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001")])
    secret = tmp_path / "secret.json"
    secret.write_text('{"secret":true}', encoding="utf-8")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "raw/../../secret.json"
    manifest["manifest_sha256"] = manifest_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = verify_cohort(str(out))

    assert result["ok"] is False
    assert any("invalid snapshot path" in error for error in result["errors"])


def test_malformed_manifest_types_fail_without_crashing(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001")])
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["included_nct_ids"] = [{"not": "hashable"}]
    manifest["candidate_count"] = True
    manifest["manifest_sha256"] = manifest_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = verify_cohort(str(out))

    assert result["ok"] is False
    assert any("invalid NCT ID" in error for error in result["errors"])
    assert any("candidate_count must be" in error for error in result["errors"])


@pytest.mark.parametrize("artifact_name", [QUALITY_NAME, EXCLUSIONS_NAME])
def test_derived_artifact_tampering_is_detected(tmp_path, artifact_name):
    out = _discover(tmp_path, [make_study("NCT00000001")])
    artifact = out / artifact_name
    artifact.write_text(artifact.read_text(encoding="utf-8") + "tampered", encoding="utf-8")

    result = verify_cohort(str(out))

    assert result["ok"] is False
    assert any(f"{artifact_name} hash mismatch" in error for error in result["errors"])


# --- Queue: determinism, reconstruction, no efficacy label --------------------


def test_queue_is_deterministic_across_runs(tmp_path):
    studies = [make_study("NCT00000002"), make_study("NCT00000001")]
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    discover_cohort(_client(_cohort_fixture(studies)), str(out_a), retrieval_timestamp=FIXED_TIME)
    discover_cohort(_client(_cohort_fixture(studies)), str(out_b), retrieval_timestamp=FIXED_TIME)

    assert (out_a / QUEUE_NAME).read_bytes() == (out_b / QUEUE_NAME).read_bytes()
    assert load_manifest(str(out_a))["manifest_sha256"] == load_manifest(str(out_b))["manifest_sha256"]


def test_queue_reconstruction_and_rebuild(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001"), make_study("NCT00000002")])
    queue_path = out / QUEUE_NAME

    # Reorder the queue lines (tamper): verification must fail.
    lines = queue_path.read_text(encoding="utf-8").splitlines()
    queue_path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    assert verify_cohort(str(out))["ok"] is False

    # Rebuild from frozen snapshots restores it.
    rebuild = rebuild_queue(str(out))
    assert rebuild["matches_manifest"] is True
    assert verify_cohort(str(out))["ok"] is True


def test_queue_has_no_scientific_outcome_label(tmp_path):
    # A COMPLETED trial with posted results must NOT be auto-labeled met/not_met.
    out = _discover(tmp_path, [make_study("NCT00000001", overall_status="COMPLETED", has_results=True)])
    lines = (out / QUEUE_NAME).read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])

    assert "scientific_outcome" not in record
    assert record["registry_status"] == "COMPLETED"
    assert record["review_status"] == "pending"
    assert record["scientific_outcome_status"] == "unadjudicated"


def test_queue_marks_historical_snapshot_unresolved(tmp_path):
    out = _discover(tmp_path, [make_study("NCT00000001"), make_study("NCT00000002")])
    for line in (out / QUEUE_NAME).read_text(encoding="utf-8").splitlines():
        assert json.loads(line)["historical_feature_snapshot_status"] == "unresolved"


# --- CLI: discover / verify / summary / rebuild-queue -------------------------


def _patch_cli_transport(monkeypatch, studies):
    monkeypatch.setenv("CTG_USER_AGENT", "cli-test-agent")
    transport = _cohort_fixture(studies)
    monkeypatch.setattr(clinicaltrials, "_default_transport", transport)
    return transport


def test_cli_discover_verify_summary_rebuild(monkeypatch, tmp_path, capsys):
    _patch_cli_transport(monkeypatch, [make_study("NCT00000001"), make_study("NCT00000002")])
    out = str(tmp_path / "cohort")

    assert cohort_cli.main(["discover", "--output", out, "--max-studies", "3"]) == 0
    capsys.readouterr()

    assert cohort_cli.main(["verify", "--output", out]) == 0
    assert '"ok": true' in capsys.readouterr().out

    assert cohort_cli.main(["summary", "--output", out]) == 0
    summary_out = capsys.readouterr().out
    assert "NCT00000001" in summary_out
    assert "protocolSection" not in summary_out  # never prints raw payloads

    assert cohort_cli.main(["rebuild-queue", "--output", out]) == 0


def test_cli_discover_refuses_overwrite(monkeypatch, tmp_path, capsys):
    _patch_cli_transport(monkeypatch, [make_study("NCT00000001")])
    out = str(tmp_path / "cohort")

    assert cohort_cli.main(["discover", "--output", out]) == 0
    capsys.readouterr()
    # Second run without --force must refuse.
    assert cohort_cli.main(["discover", "--output", out]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    # With --force it succeeds.
    assert cohort_cli.main(["discover", "--output", out, "--force"]) == 0


def test_force_replaces_entire_cohort_without_stale_snapshots(tmp_path):
    out = tmp_path / "cohort"
    first_studies = [
        make_study("NCT00000001"),
        make_study("NCT00000002"),
    ]
    discover_cohort(
        _client(_cohort_fixture(first_studies)),
        str(out),
        retrieval_timestamp=FIXED_TIME,
    )

    replacement = [make_study("NCT00000003")]
    discover_cohort(
        _client(_cohort_fixture(replacement)),
        str(out),
        force=True,
        retrieval_timestamp=FIXED_TIME,
    )

    assert not (out / RAW_DIR / "NCT00000001.json").exists()
    assert not (out / RAW_DIR / "NCT00000002.json").exists()
    assert (out / RAW_DIR / "NCT00000003.json").exists()
    assert verify_cohort(str(out))["ok"] is True


def test_partial_output_without_manifest_is_protected(tmp_path):
    out = tmp_path / "cohort"
    out.mkdir()
    (out / "partial.txt").write_text("incomplete prior run", encoding="utf-8")

    with pytest.raises(CohortError, match="refusing to overwrite"):
        discover_cohort(
            _client(_cohort_fixture([make_study("NCT00000001")])),
            str(out),
            retrieval_timestamp=FIXED_TIME,
        )


def test_cli_rejects_invalid_scan_bounds(monkeypatch, tmp_path, capsys):
    _patch_cli_transport(monkeypatch, [make_study("NCT00000001")])
    out = str(tmp_path / "cohort")

    assert cohort_cli.main(["discover", "--output", out, "--page-size", "0"]) == 1
    assert "--page-size" in capsys.readouterr().err
    assert cohort_cli.main(
        ["discover", "--output", out, "--scan-page-limit", "0"]
    ) == 1
    assert "--scan-page-limit" in capsys.readouterr().err


def test_cli_discover_missing_user_agent(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("CTG_USER_AGENT", raising=False)
    rc = cohort_cli.main(["discover", "--output", str(tmp_path / "c")])
    assert rc == 1
    assert "CTG_USER_AGENT" in capsys.readouterr().err


def test_discover_overwrite_protection_direct(tmp_path):
    studies = [make_study("NCT00000001")]
    out = tmp_path / "cohort"
    discover_cohort(_client(_cohort_fixture(studies)), str(out), retrieval_timestamp=FIXED_TIME)
    with pytest.raises(CohortError):
        discover_cohort(_client(_cohort_fixture(studies)), str(out), retrieval_timestamp=FIXED_TIME)
