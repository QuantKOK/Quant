#!/usr/bin/env python
"""CLI to turn offline Kalshi-style market JSON into MacroEdge contract observations.

Subcommands:

* ``validate --input PATH --event-type TYPE``            - validate a Kalshi market as a contract record
* ``emit --input PATH --output PATH --event-type TYPE``  - write the canonical contract record as JSON
* ``append --input PATH --ledger PATH --event-type TYPE`` - append the observation to a hash-chained ledger
* ``batch-append --input-dir DIR --ledger PATH``          - append many offline fixtures to a ledger

This bridges the offline Kalshi adapter -> platform-neutral contract record ->
contract observation ledger. It is offline only: it never contacts the Kalshi
API, uses no credentials, and never places or executes trades. Inputs are raw
Kalshi-style JSON fixtures.

``event_type`` must be explicit (a supported MacroEdge event type). Pass
``--observed-at`` and ``--observation-id`` for a reproducible ``contract_hash``.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
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
    with open(path, "r", encoding="utf-8-sig") as handle:
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


def cmd_batch_append(args: argparse.Namespace) -> int:
    try:
        paths = _batch_input_paths(args)
        if not paths:
            raise ContractError("no input files matched")
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"batch append failed: {exc}", file=sys.stderr)
        return 1

    prepared = []
    errors = []
    for path in paths:
        try:
            draft = kalshi_market_to_contract_draft(
                _load_market(path),
                observed_at=args.observed_at,
                event_type=args.event_type,
            )
            prepared.append(
                {
                    "path": path,
                    "draft": draft,
                    "observation_id": _batch_observation_id(args.id_prefix, path),
                }
            )
        except (ContractError, OSError, json.JSONDecodeError) as exc:
            errors.append(_batch_error(path, exc))
            if not args.continue_on_error:
                _print_json(_batch_summary(args.ledger, paths, [], errors))
                return 1

    duplicate_id_error = _duplicate_observation_id_error(prepared)
    if duplicate_id_error:
        errors.append(duplicate_id_error)
        _print_json(_batch_summary(args.ledger, paths, [], errors))
        return 1

    records = []
    for item in prepared:
        try:
            record = append_observation(
                args.ledger,
                item["draft"],
                observation_id=item["observation_id"],
            )
            record_summary = _record_summary(record)
            record_summary["input"] = os.path.abspath(item["path"])
            record_summary["previous_hash"] = record["previous_hash"]
            record_summary["ledger_hash"] = record["ledger_hash"]
            records.append(record_summary)
        except (ContractError, OSError, json.JSONDecodeError) as exc:
            errors.append(_batch_error(item["path"], exc))
            if not args.continue_on_error:
                break

    summary = _batch_summary(args.ledger, paths, records, errors)
    _print_json(summary)
    return 0 if summary["ok"] else 1


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


def _batch_input_paths(args: argparse.Namespace) -> list[str]:
    if args.input_dir:
        pattern = os.path.join(args.input_dir, args.glob)
        return [
            os.path.abspath(path)
            for path in sorted(glob.glob(pattern, recursive=args.recursive))
            if os.path.isfile(path)
        ]
    return _manifest_paths(args.manifest)


def _manifest_paths(path: str) -> list[str]:
    manifest_path = os.path.abspath(path)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        if path.lower().endswith(".json"):
            payload = json.load(handle)
            if isinstance(payload, dict):
                payload = payload.get("inputs")
            if not isinstance(payload, list):
                raise ContractError("manifest JSON must be a list or an object with an inputs list")
            raw_paths = payload
        else:
            raw_paths = [
                line.strip()
                for line in handle
                if line.strip() and not line.lstrip().startswith("#")
            ]

    paths = []
    base_dir = os.path.dirname(manifest_path)
    for item in raw_paths:
        if not isinstance(item, str) or not item.strip():
            raise ContractError("manifest inputs must be non-empty strings")
        candidate = item.strip()
        if not os.path.isabs(candidate):
            candidate = os.path.join(base_dir, candidate)
        paths.append(os.path.abspath(candidate))
    return paths


def _batch_observation_id(prefix: str | None, path: str) -> str | None:
    if not prefix:
        return None
    stem = os.path.splitext(os.path.basename(path))[0]
    clean_stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-") or "market"
    clean_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", prefix).strip("-") or "kalshi"
    return f"{clean_prefix}-{clean_stem}"


def _duplicate_observation_id_error(items: list[dict]) -> dict | None:
    seen = set()
    for item in items:
        observation_id = item["observation_id"]
        if observation_id is None:
            continue
        if observation_id in seen:
            return {
                "input": "<batch>",
                "error": f"duplicate generated observation_id: {observation_id}",
            }
        seen.add(observation_id)
    return None


def _batch_error(path: str, exc: BaseException) -> dict:
    return {
        "input": os.path.abspath(path),
        "error": str(exc),
    }


def _batch_summary(ledger: str, paths: list[str], records: list[dict], errors: list[dict]) -> dict:
    return {
        "ok": not errors,
        "ledger": os.path.abspath(ledger),
        "input_count": len(paths),
        "appended_count": len(records),
        "error_count": len(errors),
        "records": records,
        "errors": errors,
    }


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

    batch_p = subparsers.add_parser("batch-append", help="Append many offline Kalshi fixtures to a ledger")
    source_group = batch_p.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--input-dir", help="Directory of raw Kalshi-style JSON files")
    source_group.add_argument("--manifest", help="JSON/text manifest of raw Kalshi-style JSON files")
    batch_p.add_argument("--glob", default="*.json", help="File glob within --input-dir (default: *.json)")
    batch_p.add_argument("--recursive", action="store_true", help="Allow recursive glob patterns such as **/*.json")
    batch_p.add_argument("--ledger", required=True, help="Append-only observation ledger JSONL path")
    batch_p.add_argument("--event-type", default=None, help="Explicit MacroEdge event type for every input (or use macroedge_event_type in fixtures)")
    batch_p.add_argument("--observed-at", default=None, help="Optional fixed ISO-8601 observed_at for every input")
    batch_p.add_argument("--id-prefix", default=None, help="Optional deterministic observation_id prefix; file stems are appended")
    batch_p.add_argument("--continue-on-error", action="store_true", help="Append valid inputs even when some inputs fail")
    batch_p.set_defaults(func=cmd_batch_append)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
