#!/usr/bin/env python
"""CLI to turn offline Kalshi-style market JSON into MacroEdge contract observations.

Subcommands:

* ``validate --input PATH --event-type TYPE``            - validate a Kalshi market as a contract record
* ``emit --input PATH --output PATH --event-type TYPE``  - write the canonical contract record as JSON
* ``append --input PATH --ledger PATH --event-type TYPE`` - append the observation to a hash-chained ledger

This bridges the offline Kalshi adapter -> platform-neutral contract record ->
contract observation ledger. It is offline only: it never contacts the Kalshi
API, uses no credentials, and never places or executes trades. Inputs are raw
Kalshi-style JSON fixtures.

``event_type`` must be explicit (a supported MacroEdge event type). Pass
``--observed-at`` and ``--observation-id`` for a reproducible ``contract_hash``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

# Allow running as a script: put the repo root (parent of ``macroedge``) on sys.path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from macroedge.adapters.kalshi import (  # type: ignore
    build_contract_record_from_kalshi,
    kalshi_market_to_contract_draft,
)
from macroedge.contracts import ContractError  # type: ignore
from macroedge.contract_ledger import append_observation  # type: ignore


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_market(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _record_summary(record: dict) -> dict:
    return {
        "ok": True,
        "observation_id": record["observation_id"],
        "contract_hash": record["contract_hash"],
        "event_type": record["event"]["event_type"],
        "platform": record["market"]["platform"],
        "contract_id": record["market"]["contract_id"],
        "settlement_datetime": record["event"]["settlement_datetime"],
        "implied_probability_source": record["prices"]["implied_probability_source"],
    }


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        record = build_contract_record_from_kalshi(
            _load_market(args.input),
            observed_at=args.observed_at,
            event_type=args.event_type,
            observation_id=args.observation_id,
        )
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"validate failed: {exc}", file=sys.stderr)
        return 1
    _print_json(_record_summary(record))
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    try:
        record = build_contract_record_from_kalshi(
            _load_market(args.input),
            observed_at=args.observed_at,
            event_type=args.event_type,
            observation_id=args.observation_id,
        )
        _atomic_write_json(args.output, record)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"emit failed: {exc}", file=sys.stderr)
        return 1
    summary = _record_summary(record)
    summary["output"] = os.path.abspath(args.output)
    _print_json(summary)
    return 0


def cmd_append(args: argparse.Namespace) -> int:
    try:
        draft = kalshi_market_to_contract_draft(
            _load_market(args.input),
            observed_at=args.observed_at,
            event_type=args.event_type,
        )
        record = append_observation(
            args.ledger,
            draft,
            observation_id=args.observation_id,
        )
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"append failed: {exc}", file=sys.stderr)
        return 1
    summary = _record_summary(record)
    summary["ledger"] = os.path.abspath(args.ledger)
    summary["previous_hash"] = record["previous_hash"]
    summary["ledger_hash"] = record["ledger_hash"]
    _print_json(summary)
    return 0


def _atomic_write_json(output: str, record: dict) -> None:
    output_path = os.path.abspath(output)
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=output_dir, delete=False
        ) as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = handle.name
        os.replace(temp_path, output_path)
    except OSError:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _add_common_market_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True, help="Raw Kalshi-style market JSON path")
    parser.add_argument("--event-type", default=None, help="Explicit MacroEdge event type (or use macroedge_event_type in the fixture)")
    parser.add_argument("--observed-at", default=None, help="Optional fixed ISO-8601 observed_at (for reproducibility)")
    parser.add_argument("--observation-id", default=None, help="Optional fixed observation id (for reproducibility)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline Kalshi fixture -> MacroEdge contract observation tools."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_p = subparsers.add_parser("validate", help="Validate a Kalshi market as a contract record")
    _add_common_market_args(validate_p)
    validate_p.set_defaults(func=cmd_validate)

    emit_p = subparsers.add_parser("emit", help="Write the canonical contract record as JSON")
    _add_common_market_args(emit_p)
    emit_p.add_argument("--output", required=True, help="Output contract record JSON path")
    emit_p.set_defaults(func=cmd_emit)

    append_p = subparsers.add_parser("append", help="Append the observation to a hash-chained ledger")
    _add_common_market_args(append_p)
    append_p.add_argument("--ledger", required=True, help="Append-only observation ledger JSONL path")
    append_p.set_defaults(func=cmd_append)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
