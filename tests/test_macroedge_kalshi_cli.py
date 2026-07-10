import json
from pathlib import Path

import pytest

from macroedge.app import kalshi as kalshi_cli
from macroedge.contract_ledger import verify_ledger
from macroedge.contracts import verify_contract_record

CPI = Path("macroedge/examples/kalshi-market-cpi.example.json")
T1 = "2026-07-14T20:00:00-05:00"
T2 = "2026-07-14T21:00:00-05:00"


def load_cpi():
    return json.loads(CPI.read_text(encoding="utf-8"))


def write_market(tmp_path, market, name="market.json"):
    path = tmp_path / name
    path.write_text(json.dumps(market), encoding="utf-8")
    return path


# --- validate -----------------------------------------------------------------


def test_cli_validate_happy(capsys):
    rc = kalshi_cli.main(["validate", "--input", str(CPI), "--event-type", "cpi"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"ok": true' in out
    assert '"event_type": "cpi"' in out
    assert '"contract_hash"' in out


def test_cli_validate_requires_event_type(tmp_path, capsys):
    market = load_cpi()
    market.pop("macroedge_event_type")
    path = write_market(tmp_path, market)

    rc = kalshi_cli.main(["validate", "--input", str(path)])

    assert rc == 1
    assert "event_type is required" in capsys.readouterr().err


def test_cli_validate_rejects_bad_market(tmp_path, capsys):
    market = load_cpi()
    market.pop("ticker")
    path = write_market(tmp_path, market)

    rc = kalshi_cli.main(["validate", "--input", str(path), "--event-type", "cpi"])

    assert rc == 1
    assert "validate failed" in capsys.readouterr().err


# --- emit ---------------------------------------------------------------------


def test_cli_emit_writes_valid_and_deterministic_record(tmp_path, capsys):
    out1 = tmp_path / "obs1.json"
    out2 = tmp_path / "obs2.json"
    args = ["--input", str(CPI), "--event-type", "cpi", "--observed-at", T1, "--observation-id", "obs-fixed"]

    assert kalshi_cli.main(["emit", "--output", str(out1), *args]) == 0
    capsys.readouterr()
    assert kalshi_cli.main(["emit", "--output", str(out2), *args]) == 0
    capsys.readouterr()

    record = json.loads(out1.read_text(encoding="utf-8"))
    assert verify_contract_record(record)["ok"] is True
    assert record["observation_id"] == "obs-fixed"
    # Reproducible: same inputs -> identical bytes.
    assert out1.read_bytes() == out2.read_bytes()


def test_cli_emit_fails_on_bad_market_and_writes_nothing(tmp_path, capsys):
    market = load_cpi()
    market.pop("rules_primary")
    path = write_market(tmp_path, market)
    out = tmp_path / "obs.json"

    rc = kalshi_cli.main(["emit", "--input", str(path), "--output", str(out), "--event-type", "cpi"])

    assert rc == 1
    assert "emit failed" in capsys.readouterr().err
    assert not out.exists()


# --- append -------------------------------------------------------------------


def test_cli_append_chains_and_verifies(tmp_path, capsys):
    ledger = tmp_path / "observations.jsonl"

    assert kalshi_cli.main(
        ["append", "--input", str(CPI), "--ledger", str(ledger), "--event-type", "cpi",
         "--observed-at", T1, "--observation-id", "obs-1"]
    ) == 0
    first_out = capsys.readouterr().out
    assert '"previous_hash": "' + "0" * 64 + '"' in first_out

    assert kalshi_cli.main(
        ["append", "--input", str(CPI), "--ledger", str(ledger), "--event-type", "cpi",
         "--observed-at", T2, "--observation-id", "obs-2"]
    ) == 0
    capsys.readouterr()

    result = verify_ledger(str(ledger))
    assert result["ok"] is True, result["errors"]
    assert result["record_count"] == 2


def test_cli_append_rejects_duplicate_observation_id(tmp_path, capsys):
    ledger = tmp_path / "observations.jsonl"
    kalshi_cli.main(
        ["append", "--input", str(CPI), "--ledger", str(ledger), "--event-type", "cpi",
         "--observed-at", T1, "--observation-id", "dup"]
    )
    capsys.readouterr()

    rc = kalshi_cli.main(
        ["append", "--input", str(CPI), "--ledger", str(ledger), "--event-type", "cpi",
         "--observed-at", T2, "--observation-id", "dup"]
    )

    assert rc == 1
    assert "duplicate observation_id" in capsys.readouterr().err


def test_cli_append_rejects_invalid_input(tmp_path, capsys):
    ledger = tmp_path / "observations.jsonl"
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")

    rc = kalshi_cli.main(["append", "--input", str(bad), "--ledger", str(ledger), "--event-type", "cpi"])

    assert rc == 1
    assert "append failed" in capsys.readouterr().err
    assert not ledger.exists()


# --- offline guarantee --------------------------------------------------------


def test_cli_module_is_offline_only():
    source = Path("macroedge/app/kalshi.py").read_text(encoding="utf-8")
    for banned in ("urllib", "requests", "http.client", "socket", "httpx", "aiohttp", "websocket"):
        assert banned not in source, f"network import '{banned}' must not appear in the offline CLI"
