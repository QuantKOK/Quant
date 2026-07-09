#!/usr/bin/env python
"""CLI for offline MacroEdge market-contract observations.

Subcommands:

* ``validate --input PATH``              - validate one contract draft and print a summary
* ``emit --input PATH --output PATH``    - write the canonical contract record as JSON
* ``verify --input PATH``                - verify an emitted contract record
* ``append --input PATH --ledger PATH``  - append a contract observation to a ledger
* ``verify-ledger --ledger PATH``        - verify an observation ledger
* ``head --ledger PATH``                 - print the current observation-ledger head

This tool is offline only. It never contacts a prediction-market API and never
places or executes trades.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

# Allow running as a script: put the repo root (parent of ``macroedge``) on sys.path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from macroedge.contracts import ContractError, build_contract_record, verify_contract_record  # type: ignore
from macroedge.contract_ledger import append_observation, verify_ledger  # type: ignore


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
    temp_path: str | None = None
    try:
        record = _build_record(args)
        output_path = os.path.abspath(args.output)
        output_dir = os.path.dirname(output_path) or "."
        os.makedirs(output_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=output_dir,
            delete=False,
        ) as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = handle.name
        os.replace(temp_path, output_path)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
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


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        result = verify_contract_record(_load_draft(args.input))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0 if result["ok"] else 1


def cmd_append(args: argparse.Namespace) -> int:
    try:
        record = append_observation(
            args.ledger,
            _load_draft(args.input),
            observed_at=args.observed_at,
            observation_id=args.observation_id,
        )
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"append failed: {exc}", file=sys.stderr)
        return 1
    _print_json(
        {
            "ok": True,
            "ledger": os.path.abspath(args.ledger),
            "observation_id": record["observation_id"],
            "contract_hash": record["contract_hash"],
            "previous_hash": record["previous_hash"],
            "ledger_hash": record["ledger_hash"],
        }
    )
    return 0


def cmd_verify_ledger(args: argparse.Namespace) -> int:
    result = verify_ledger(args.ledger)
    _print_json(
        {
            "ok": result["ok"],
            "record_count": result["record_count"],
            "head_hash": result["head_hash"],
            "last_observed_at": result["last_observed_at"],
            "errors": result["errors"],
        }
    )
    return 0 if result["ok"] else 1


def cmd_head(args: argparse.Namespace) -> int:
    result = verify_ledger(args.ledger)
    print(result["head_hash"])
    return 0 if result["ok"] else 1


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

    verify_p = subparsers.add_parser("verify", help="Verify an emitted contract observation JSON")
    verify_p.add_argument("--input", required=True, help="Emitted contract record JSON path")
    verify_p.set_defaults(func=cmd_verify)

    append_p = subparsers.add_parser("append", help="Append a contract observation to a JSONL ledger")
    append_p.add_argument("--input", required=True, help="Contract draft JSON path")
    append_p.add_argument("--ledger", required=True, help="Append-only observation ledger JSONL path")
    append_p.add_argument("--observed-at", default=None, help="Optional fixed ISO-8601 observed_at")
    append_p.add_argument("--observation-id", default=None, help="Optional fixed observation id")
    append_p.set_defaults(func=cmd_append)

    verify_ledger_p = subparsers.add_parser("verify-ledger", help="Verify an observation ledger")
    verify_ledger_p.add_argument("--ledger", required=True, help="Append-only observation ledger JSONL path")
    verify_ledger_p.set_defaults(func=cmd_verify_ledger)

    head_p = subparsers.add_parser("head", help="Print the current observation-ledger head hash")
    head_p.add_argument("--ledger", required=True, help="Append-only observation ledger JSONL path")
    head_p.set_defaults(func=cmd_head)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
