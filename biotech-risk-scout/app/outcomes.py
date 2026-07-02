#!/usr/bin/env python
"""CLI for the auditable historical clinical-trial outcome dataset.

Subcommands:

* ``build``   - validate a source file and write canonical JSONL + manifest
* ``verify``  - check an existing dataset against its manifest (tamper detection)
* ``summary`` - print record/label/registry-status counts for a dataset

This tool records observed scientific outcomes for risk research. It is not
investment advice and never derives outcomes from registry status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scout.outcomes.dataset import (  # type: ignore
    build_dataset,
    summarize_dataset,
    verify_dataset,
)
from scout.outcomes.schema import OutcomeValidationError  # type: ignore


def _default_manifest_path(dataset_path: str) -> str:
    base, _ = os.path.splitext(dataset_path)
    return f"{base}.manifest.json"


def cmd_build(args: argparse.Namespace) -> int:
    manifest_path = args.manifest or _default_manifest_path(args.out)
    try:
        manifest = build_dataset(
            args.source,
            args.out,
            manifest_path,
            generated_at=args.generated_at,
        )
    except (OutcomeValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Built {manifest['record_count']} record(s) -> {args.out}")
    print(f"Manifest -> {manifest_path}")
    print(f"Dataset SHA-256: {manifest['dataset_sha256']}")
    print(f"Label counts: {json.dumps(manifest['label_counts'], sort_keys=True)}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    manifest_path = args.manifest or _default_manifest_path(args.dataset)
    result = verify_dataset(args.dataset, manifest_path)
    if result.ok:
        print(f"OK: {result.record_count} record(s) verified against manifest.")
        print(f"Dataset SHA-256: {result.computed_sha256}")
        return 0
    print("VERIFICATION FAILED:", file=sys.stderr)
    for error in result.errors:
        print(f"  - {error}", file=sys.stderr)
    return 1


def cmd_summary(args: argparse.Namespace) -> int:
    try:
        summary = summarize_dataset(args.dataset)
    except (OutcomeValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"Summary failed: {exc}", file=sys.stderr)
        return 1

    print("Historical Outcome Dataset Summary")
    print("----------------------------------")
    print(f"Schema version: {summary['schema_version']}")
    print(f"Records: {summary['record_count']}")
    print(f"Dataset SHA-256: {summary['dataset_sha256']}")
    print("Scientific outcome labels:")
    for label, count in sorted(summary["label_counts"].items()):
        print(f"  {label}: {count}")
    print("Registry statuses (kept separate from outcomes):")
    for status, count in summary["registry_status_counts"].items():
        print(f"  {status}: {count}")
    print("Note: registry status is not a scientific outcome. Diligence/research only, not investment advice.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, verify, and summarize the historical outcome dataset."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_p = subparsers.add_parser("build", help="Validate a source file and write dataset + manifest")
    build_p.add_argument("--source", required=True, help="Source JSON/JSONL file")
    build_p.add_argument("--out", required=True, help="Output canonical JSONL dataset path")
    build_p.add_argument("--manifest", help="Manifest output path (default: <out>.manifest.json)")
    build_p.add_argument(
        "--generated-at",
        help="Optional fixed ISO-8601 generation time for reproducible manifests",
    )
    build_p.set_defaults(func=cmd_build)

    verify_p = subparsers.add_parser("verify", help="Verify a dataset against its manifest")
    verify_p.add_argument("--dataset", required=True, help="Canonical JSONL dataset path")
    verify_p.add_argument("--manifest", help="Manifest path (default: <dataset>.manifest.json)")
    verify_p.set_defaults(func=cmd_verify)

    summary_p = subparsers.add_parser("summary", help="Print counts for a dataset")
    summary_p.add_argument("--dataset", required=True, help="Canonical JSONL dataset path")
    summary_p.set_defaults(func=cmd_summary)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
