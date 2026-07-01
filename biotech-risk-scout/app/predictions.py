#!/usr/bin/env python
"""Manage the tamper-evident clinical-trial prediction ledger."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scout.predictions import (  # type: ignore
    PredictionLedgerError,
    append_prediction,
    verify_ledger,
)


DEFAULT_LEDGER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "predictions", "ledger.jsonl")
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Append and verify timestamped clinical-trial risk predictions."
    )
    parser.add_argument("--ledger", default=DEFAULT_LEDGER, help="Prediction ledger JSONL path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    append_parser = subparsers.add_parser("append", help="Append a prediction draft")
    append_parser.add_argument("--input", required=True, help="Prediction draft JSON file")

    subparsers.add_parser("verify", help="Verify the full ledger hash chain")
    subparsers.add_parser("head", help="Print the current ledger head hash")
    args = parser.parse_args(argv)

    if args.command == "append":
        try:
            with open(args.input, "r", encoding="utf-8") as handle:
                draft = json.load(handle)
            record = append_prediction(args.ledger, draft)
        except (OSError, json.JSONDecodeError, PredictionLedgerError) as exc:
            print(f"Prediction append failed: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "prediction_id": record["prediction_id"],
                    "record_hash": record["record_hash"],
                    "ledger": os.path.abspath(args.ledger),
                },
                indent=2,
            )
        )
        return 0

    verification = verify_ledger(args.ledger)
    if args.command == "head":
        print(verification["head_hash"])
        return 0 if verification["ok"] else 1

    public_result = {
        key: value
        for key, value in verification.items()
        if key != "prediction_ids"
    }
    print(json.dumps(public_result, indent=2))
    return 0 if verification["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
