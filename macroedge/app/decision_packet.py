#!/usr/bin/env python
"""CLI for offline MacroEdge decision packets (pre-trade research notes).

Subcommands:

* ``validate --input OBSERVATION ...`` - build a packet and print its summary
* ``emit --input OBSERVATION --output PATH ...`` - write the canonical packet JSON
* ``verify --input PACKET`` - re-verify an emitted packet (hash + schema + math)

A decision packet is a research artifact captured before a possible trade becomes
a logged candidate: it records the analyst's thesis, fair probability,
disconfirming evidence, intent, and an explicit decision. It is offline only: no
network, no credentials, and it never places or executes a trade. The
``suggested_next_action`` field is a workflow label, not investment advice.

Pass ``--packet-id`` and ``--created-at`` for a reproducible ``packet_hash``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

# Allow running as a script: put the repo root (parent of ``macroedge``) on sys.path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from macroedge.decision_packet import (  # type: ignore
    DEFAULT_MIN_EDGE,
    DecisionPacketError,
    build_decision_packet,
    verify_decision_packet,
)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _packet_summary(record: dict) -> dict:
    assessment = record["assessment"]
    return {
        "ok": True,
        "packet_id": record["packet_id"],
        "packet_hash": record["packet_hash"],
        "decision": record["decision"],
        "intent": record["intent"],
        "side": record["market"]["side"],
        "market_implied_probability": assessment["market_implied_probability"],
        "fair_probability": record["thesis"]["fair_probability"],
        "edge_percentage_points": assessment["edge_percentage_points"],
        "clears_edge_threshold": assessment["clears_edge_threshold"],
        "suggested_next_action": assessment["suggested_next_action"],
    }


def _build_from_args(args: argparse.Namespace) -> dict:
    return build_decision_packet(
        _load_json(args.input),
        side=args.side,
        fair_probability=args.fair_probability,
        thesis_summary=args.thesis_summary,
        data_sources=args.data_source,
        intent=args.intent,
        decision=args.decision,
        risk_notes=args.risk_notes,
        disconfirming_evidence=args.disconfirming_evidence,
        evidence_as_of=args.evidence_as_of,
        min_edge_required=args.min_edge,
        packet_id=args.packet_id,
        created_at=args.created_at,
    )


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        record = _build_from_args(args)
    except (DecisionPacketError, OSError, json.JSONDecodeError) as exc:
        print(f"validate failed: {exc}", file=sys.stderr)
        return 1
    _print_json(_packet_summary(record))
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    try:
        record = _build_from_args(args)
        _atomic_write_json(args.output, record)
    except (DecisionPacketError, OSError, json.JSONDecodeError) as exc:
        print(f"emit failed: {exc}", file=sys.stderr)
        return 1
    summary = _packet_summary(record)
    summary["output"] = os.path.abspath(args.output)
    _print_json(summary)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        result = verify_decision_packet(_load_json(args.input))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0 if result["ok"] else 1


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


def _add_build_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True, help="Verified contract observation JSON path")
    parser.add_argument("--side", required=True, choices=["YES", "NO", "yes", "no"], help="Selected side")
    parser.add_argument("--fair-probability", required=True, type=float, help="Analyst fair probability for the selected side")
    parser.add_argument("--thesis-summary", required=True, help="Concise human thesis for the selected side")
    parser.add_argument("--data-source", action="append", required=True, help="Thesis source URL; repeat for multiple")
    parser.add_argument("--intent", required=True, choices=["no_trade", "paper", "tiny_live"], help="Execution intent")
    parser.add_argument("--decision", required=True, choices=["observe_only", "candidate_ok", "reject"], help="Research decision")
    parser.add_argument("--risk-notes", required=True, help="Risk notes for this potential trade")
    parser.add_argument("--disconfirming-evidence", required=True, help="Evidence that argues against the thesis")
    parser.add_argument("--evidence-as-of", default=None, help="Optional ISO evidence timestamp (defaults to observation observed_at)")
    parser.add_argument("--min-edge", default=DEFAULT_MIN_EDGE, type=float, help="Minimum edge required to clear the threshold")
    parser.add_argument("--packet-id", default=None, help="Optional fixed packet id (for reproducibility)")
    parser.add_argument("--created-at", default=None, help="Optional fixed ISO created_at (for reproducibility)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline pre-trade decision packet tools (research notes, not trades)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_p = subparsers.add_parser("validate", help="Build a packet and print its summary")
    _add_build_args(validate_p)
    validate_p.set_defaults(func=cmd_validate)

    emit_p = subparsers.add_parser("emit", help="Write the canonical decision packet JSON")
    _add_build_args(emit_p)
    emit_p.add_argument("--output", required=True, help="Output decision packet JSON path")
    emit_p.set_defaults(func=cmd_emit)

    verify_p = subparsers.add_parser("verify", help="Re-verify an emitted decision packet")
    verify_p.add_argument("--input", required=True, help="Emitted decision packet JSON path")
    verify_p.set_defaults(func=cmd_verify)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
