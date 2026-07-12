import json
from pathlib import Path

from macroedge import __main__ as macroedge_cli


CONTRACT_EXAMPLE = Path("macroedge/examples/contract-draft.example.json")


def test_dispatcher_shows_help_without_command(capsys):
    assert macroedge_cli.main([]) == 0
    out = capsys.readouterr().out
    assert "MacroEdge offline macro event-contract research tools" in out
    assert "contracts" in out
    assert "journal" in out
    assert "kalshi" in out
    assert "demo" in out


def test_dispatcher_routes_contracts_validate(capsys):
    assert macroedge_cli.main(
        [
            "contracts",
            "validate",
            "--input",
            str(CONTRACT_EXAMPLE),
            "--observation-id",
            "dispatcher-contract",
            "--observed-at",
            "2026-07-14T20:00:00-05:00",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["observation_id"] == "dispatcher-contract"
    assert payload["event_type"] == "cpi"


def test_dispatcher_routes_journal_validate(capsys):
    assert macroedge_cli.main(
        [
            "journal",
            "validate",
            "--input",
            "macroedge/examples/trade-draft.example.json",
            "--created-at",
            "2026-07-15T02:00:00+00:00",
            "--candidate-id",
            "dispatcher-candidate",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["candidate_id"] == "dispatcher-candidate"
    assert payload["edge_percentage_points"] == 11.0
