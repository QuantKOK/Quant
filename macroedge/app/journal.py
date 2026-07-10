#!/usr/bin/env python
"""CLI for the append-only MacroEdge trade-candidate journal.

Subcommands:

* ``validate --input PATH``                 - validate one trade-candidate draft
* ``draft-from-observation --input PATH``   - seed a candidate draft from an observation
* ``append --input PATH --ledger PATH``     - append a validated candidate (hash-chained)
* ``verify --ledger PATH``                  - verify the ledger hash chain and rules
* ``summary --ledger PATH``                 - summarize a verified candidate ledger
* ``head --ledger PATH``                    - print the current ledger head hash

This is a probability-research journal for macro event contracts. It is offline
only: it never contacts a market/API and never places or executes a trade.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

# Allow running as a script: put the repo root (parent of ``macroedge``) on sys.path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from macroedge.candidate_builder import (  # type: ignore
    DEFAULT_EXIT_PLAN,
    CandidateBuilderError,
    build_candidate_from_observation,
    build_trade_draft_from_observation,
)
from macroedge.journal import TradeJournalError, build_trade_candidate  # type: ignore
from macroedge.ledger import append_candidate, summarize_ledger, verify_ledger  # type: ignore


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_draft(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _atomic_write_json(output: str, payload: dict) -> None:
    output_path = os.path.abspath(output)
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=output_dir, delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = handle.name
        os.replace(temp_path, output_path)
    except OSError:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise


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


def cmd_draft_from_observation(args: argparse.Namespace) -> int:
    try:
        observation = _load_draft(args.input)
        draft = build_trade_draft_from_observation(
            observation,
            side=args.side,
            fair_probability=args.fair_probability,
            thesis_summary=args.thesis_summary,
            data_sources=args.data_source,
            active_bankroll_usd=args.active_bankroll_usd,
            planned_risk_usd=args.planned_risk_usd,
            evidence_as_of=args.evidence_as_of,
            current_event_exposure_usd=args.current_event_exposure_usd,
            max_risk_per_trade_usd=args.max_risk_per_trade_usd,
            max_event_exposure_usd=args.max_event_exposure_usd,
            min_edge_required=args.min_edge_required,
            exit_plan=args.exit_plan,
        )
        candidate = build_candidate_from_observation(
            observation,
            side=args.side,
            fair_probability=args.fair_probability,
            thesis_summary=args.thesis_summary,
            data_sources=args.data_source,
            active_bankroll_usd=args.active_bankroll_usd,
            planned_risk_usd=args.planned_risk_usd,
            evidence_as_of=args.evidence_as_of,
            current_event_exposure_usd=args.current_event_exposure_usd,
            max_risk_per_trade_usd=args.max_risk_per_trade_usd,
            max_event_exposure_usd=args.max_event_exposure_usd,
            min_edge_required=args.min_edge_required,
            exit_plan=args.exit_plan,
            created_at=args.created_at,
            candidate_id=args.candidate_id,
        )
        _atomic_write_json(args.output, draft)
    except (CandidateBuilderError, TradeJournalError, OSError, json.JSONDecodeError) as exc:
        print(f"draft-from-observation failed: {exc}", file=sys.stderr)
        return 1
    _print_json(
        {
            "ok": True,
            "output": os.path.abspath(args.output),
            "candidate_id": candidate["candidate_id"],
            "observation_id": candidate["contract_observation"]["observation_id"],
            "side": candidate["market"]["side"],
            "entry_price": candidate["market"]["entry_price"],
            "fair_probability": candidate["thesis"]["fair_probability"],
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


def cmd_summary(args: argparse.Namespace) -> int:
    result = summarize_ledger(args.ledger)
    _print_json(result)
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

    draft_p = subparsers.add_parser(
        "draft-from-observation",
        help="Seed a trade-candidate draft from a verified contract observation",
    )
    draft_p.add_argument("--input", required=True, help="Emitted contract observation JSON path")
    draft_p.add_argument("--output", required=True, help="Output trade-candidate draft JSON path")
    draft_p.add_argument("--side", required=True, choices=["YES", "NO", "yes", "no"], help="Candidate side")
    draft_p.add_argument(
        "--fair-probability",
        required=True,
        type=float,
        help="Your fair probability for the selected side",
    )
    draft_p.add_argument("--thesis-summary", required=True, help="Concise human thesis for the selected side")
    draft_p.add_argument(
        "--data-source",
        action="append",
        required=True,
        help="Thesis source URL; repeat for multiple sources",
    )
    draft_p.add_argument("--active-bankroll-usd", required=True, type=float, help="Active bankroll used for risk checks")
    draft_p.add_argument("--planned-risk-usd", required=True, type=float, help="Planned dollars at risk")
    draft_p.add_argument("--evidence-as-of", default=None, help="Optional ISO evidence timestamp; defaults to observation observed_at")
    draft_p.add_argument("--created-at", default=None, help="Optional fixed ISO created_at for validation")
    draft_p.add_argument("--candidate-id", default=None, help="Optional fixed candidate id for validation")
    draft_p.add_argument("--current-event-exposure-usd", default=0.0, type=float, help="Current dollars already exposed to this event")
    draft_p.add_argument("--max-risk-per-trade-usd", default=25.0, type=float, help="Per-trade risk cap")
    draft_p.add_argument("--max-event-exposure-usd", default=50.0, type=float, help="Event exposure cap")
    draft_p.add_argument("--min-edge-required", default=0.08, type=float, help="Minimum required edge as a probability")
    draft_p.add_argument("--exit-plan", default=DEFAULT_EXIT_PLAN, help="Exit plan text")
    draft_p.set_defaults(func=cmd_draft_from_observation)

    append_p = subparsers.add_parser("append", help="Append a validated candidate to the ledger")
    append_p.add_argument("--input", required=True, help="Trade-candidate draft JSON path")
    append_p.add_argument("--ledger", required=True, help="Append-only ledger JSONL path")
    append_p.add_argument("--created-at", default=None, help="Optional fixed ISO-8601 created_at (else now)")
    append_p.add_argument("--candidate-id", default=None, help="Optional fixed candidate id (else a UUID)")
    append_p.set_defaults(func=cmd_append)

    verify_p = subparsers.add_parser("verify", help="Verify the ledger hash chain and rules")
    verify_p.add_argument("--ledger", required=True, help="Append-only ledger JSONL path")
    verify_p.set_defaults(func=cmd_verify)

    summary_p = subparsers.add_parser("summary", help="Summarize a verified candidate ledger")
    summary_p.add_argument("--ledger", required=True, help="Append-only ledger JSONL path")
    summary_p.set_defaults(func=cmd_summary)

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
