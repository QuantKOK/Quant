#!/usr/bin/env python
"""CLI for the append-only MacroEdge trade-candidate journal.

Subcommands:

* ``validate --input PATH``                 - validate one trade-candidate draft
* ``append --input PATH --ledger PATH``     - append a validated candidate (hash-chained)
* ``verify --ledger PATH``                  - verify the ledger hash chain and rules
* ``head --ledger PATH``                    - print the current ledger head hash

This is a probability-research journal for macro event contracts. It is offline
only: it never contacts a market/API and never places or executes a trade.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow running as a script: put the repo root (parent of ``macroedge``) on sys.path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from macroedge.journal import TradeJournalError, build_trade_candidate  # type: ignore
from macroedge.ledger import append_candidate, verify_ledger  # type: ignore


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_draft(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        candidate = build_trade_candidate(
            _load_draft(args.input),
            created_at=args.created_at,
            candidate_id=args.candidate_id,
        )
    except (TradeJournalError, OSError, json.JSONDecodeError) as exc:
        print(f"validate failed: {exc}", file=sys.stderr)
        return 1
    _print_json(
        {
            "ok": True,
            "candidate_id": candidate["candidate_id"],
            "event_type": candidate["event"]["event_type"],
            "side": candidate["market"]["side"],
            "edge_percentage_points": candidate["thesis"]["edge_percentage_points"],
            "candidate_hash": candidate["candidate_hash"],
        }
    )
    return 0


def cmd_append(args: argparse.Namespace) -> int:
    try:
        record = append_candidate(
            args.ledger,
            _load_draft(args.input),
            created_at=args.created_at,
            candidate_id=args.candidate_id,
        )
    except (TradeJournalError, OSError, json.JSONDecodeError) as exc:
        print(f"append failed: {exc}", file=sys.stderr)
        return 1
    _print_json(
        {
            "ok": True,
            "candidate_id": record["candidate_id"],
            "candidate_hash": record["candidate_hash"],
            "previous_hash": record["previous_hash"],
            "ledger_hash": record["ledger_hash"],
            "ledger": os.path.abspath(args.ledger),
        }
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_ledger(args.ledger)
    _print_json(
        {
            "ok": result["ok"],
            "record_count": result["record_count"],
            "head_hash": result["head_hash"],
            "last_created_at": result["last_created_at"],
            "errors": result["errors"],
        }
    )
    return 0 if result["ok"] else 1


def cmd_head(args: argparse.Namespace) -> int:
    result = verify_ledger(args.ledger)
    print(result["head_hash"])
    return 0 if result["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append-only MacroEdge trade-candidate journal.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_p = subparsers.add_parser("validate", help="Validate a trade-candidate draft")
    validate_p.add_argument("--input", required=True, help="Trade-candidate draft JSON path")
    validate_p.add_argument("--created-at", default=None, help="Optional fixed ISO-8601 created_at (else now)")
    validate_p.add_argument("--candidate-id", default=None, help="Optional fixed candidate id (else a UUID)")
    validate_p.set_defaults(func=cmd_validate)

    append_p = subparsers.add_parser("append", help="Append a validated candidate to the ledger")
    append_p.add_argument("--input", required=True, help="Trade-candidate draft JSON path")
    append_p.add_argument("--ledger", required=True, help="Append-only ledger JSONL path")
    append_p.add_argument("--created-at", default=None, help="Optional fixed ISO-8601 created_at (else now)")
    append_p.add_argument("--candidate-id", default=None, help="Optional fixed candidate id (else a UUID)")
    append_p.set_defaults(func=cmd_append)

    verify_p = subparsers.add_parser("verify", help="Verify the ledger hash chain and rules")
    verify_p.add_argument("--ledger", required=True, help="Append-only ledger JSONL path")
    verify_p.set_defaults(func=cmd_verify)

    head_p = subparsers.add_parser("head", help="Print the current ledger head hash")
    head_p.add_argument("--ledger", required=True, help="Append-only ledger JSONL path")
    head_p.set_defaults(func=cmd_head)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
