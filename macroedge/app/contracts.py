#!/usr/bin/env python
"""CLI for offline MacroEdge market-contract observations.

Subcommands:

* ``validate --input PATH``              - validate one contract draft and print a summary
* ``emit --input PATH --output PATH``    - write the canonical contract record as JSON

This tool is offline only. It never contacts a prediction-market API and never
places or executes trades.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow running as a script: put the repo root (parent of ``macroedge``) on sys.path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from macroedge.contracts import ContractError, build_contract_record  # type: ignore


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_draft(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _build_record(args: argparse.Namespace) -> dict:
    return build_contract_record(
        _load_draft(args.input),
        observed_at=args.observed_at,
        observation_id=args.observation_id,
    )


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        record = _build_record(args)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"validate failed: {exc}", file=sys.stderr)
        return 1
    _print_json(
        {
            "ok": True,
            "observation_id": record["observation_id"],
            "event_type": record["event"]["event_type"],
            "contract_id": record["market"]["contract_id"],
            "midpoint_probability": record["prices"]["midpoint_probability"],
            "spread": record["prices"]["spread"],
            "contract_hash": record["contract_hash"],
        }
    )
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    try:
        record = _build_record(args)
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"emit failed: {exc}", file=sys.stderr)
        return 1
    _print_json(
        {
            "ok": True,
            "output": os.path.abspath(args.output),
            "observation_id": record["observation_id"],
            "contract_hash": record["contract_hash"],
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline MacroEdge market-contract observation tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_p = subparsers.add_parser("validate", help="Validate a contract observation draft")
    validate_p.add_argument("--input", required=True, help="Contract draft JSON path")
    validate_p.add_argument("--observed-at", default=None, help="Optional fixed ISO-8601 observed_at")
    validate_p.add_argument("--observation-id", default=None, help="Optional fixed observation id")
    validate_p.set_defaults(func=cmd_validate)

    emit_p = subparsers.add_parser("emit", help="Write the canonical contract observation JSON")
    emit_p.add_argument("--input", required=True, help="Contract draft JSON path")
    emit_p.add_argument("--output", required=True, help="Canonical contract record JSON path")
    emit_p.add_argument("--observed-at", default=None, help="Optional fixed ISO-8601 observed_at")
    emit_p.add_argument("--observation-id", default=None, help="Optional fixed observation id")
    emit_p.set_defaults(func=cmd_emit)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
