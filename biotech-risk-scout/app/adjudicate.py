#!/usr/bin/env python
"""CLI for first-pass adjudication evidence packets (drafts for second review).

Subcommands:

* ``create-packets``      - build packets from frozen authoritative snapshots
* ``verify``              - verify a batch against its manifest + authoritative cohort
* ``summary``             - non-sensitive counts + per-NCT proposal/status
* ``export-review-batch`` - write a deterministic human second-review worksheet

Packets are drafts for a second human reviewer. They never carry final labels or
training-ready status, and never infer outcomes from registry status. Output is
written outside Git; the authoritative cohort is never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scout.adjudication.packets import (  # type: ignore
    BATCH_MANIFEST_NAME,
    PACKETS_NAME,
    BatchError,
    apply_review_file,
    create_packets,
)
from scout.adjudication.verify import verify_batch  # type: ignore

REGISTRY_URL = "https://clinicaltrials.gov/study/{nct_id}"


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_packets(output_dir: str) -> list[dict]:
    with open(os.path.join(output_dir, PACKETS_NAME), "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def cmd_create_packets(args: argparse.Namespace) -> int:
    try:
        summary = create_packets(
            args.authoritative,
            args.output,
            nct_ids=args.nct or None,
            reviewer=args.reviewer,
            reviewed_at=args.reviewed_at,
            force=args.force,
        )
    except (BatchError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"create-packets failed: {exc}", file=sys.stderr)
        return 1
    _print_json(summary)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_batch(args.output, args.authoritative)
    _print_json({"ok": result["ok"], "packet_count": result["packet_count"], "errors": result["errors"]})
    return 0 if result["ok"] else 1


def cmd_apply_reviews(args: argparse.Namespace) -> int:
    try:
        result = apply_review_file(
            args.authoritative,
            args.output,
            args.reviews,
            force=args.force,
            generated_at=args.generated_at,
        )
    except (BatchError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"apply-reviews failed: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    try:
        with open(os.path.join(args.output, BATCH_MANIFEST_NAME), "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        packets = _load_packets(args.output)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"summary failed: {exc}", file=sys.stderr)
        return 1

    proposal_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    per_nct = {}
    for packet in sorted(packets, key=lambda p: p.get("nct_id", "")):
        outcome = packet.get("proposed_outcome")
        key = "null" if outcome is None else str(outcome)
        proposal_counts[key] = proposal_counts.get(key, 0) + 1
        status = str(packet.get("review_status"))
        status_counts[status] = status_counts.get(status, 0) + 1
        per_nct[packet.get("nct_id")] = {
            "proposed_outcome": outcome,
            "confidence": packet.get("confidence"),
            "review_status": status,
            "evidence_source_count": len(packet.get("evidence_sources", [])),
            "conflict_count": len(packet.get("conflicts", [])),
        }

    _print_json(
        {
            "batch_id": manifest.get("batch_id"),
            "packet_count": manifest.get("packet_count"),
            "proposal_counts": proposal_counts,
            "review_status_counts": status_counts,
            "per_nct": per_nct,
            "not_final_notice": manifest.get("not_final_notice"),
        }
    )
    return 0


def cmd_export_review_batch(args: argparse.Namespace) -> int:
    try:
        packets = _load_packets(args.output)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"export-review-batch failed: {exc}", file=sys.stderr)
        return 1

    lines = [
        "# Adjudication Batch — Second-Review Worksheet",
        "",
        "Drafts for a second human reviewer. Not final labels, not training data.",
        "Assign an outcome only if the pre-specified PRIMARY endpoint maps clearly to",
        "dated public evidence (publication > CT.gov results/SAP > regulator > sponsor).",
        "Preserve conflicts; do not silently resolve them.",
        "",
    ]
    for packet in sorted(packets, key=lambda p: p.get("nct_id", "")):
        nct = packet.get("nct_id", "?")
        lines.append(f"## {nct}")
        lines.append(f"- Registry: {REGISTRY_URL.format(nct_id=nct)}")
        lines.append(f"- Source snapshot: {packet.get('source_snapshot_path')} (sha256 {packet.get('source_snapshot_sha256')})")
        rr = packet.get("registry_results", {})
        lines.append(
            f"- Registry posted results: has_results={rr.get('has_posted_results')}, "
            f"first_posted={rr.get('results_first_posted_date')}, "
            f"posted_measures={rr.get('posted_outcome_measure_count')}"
        )
        lines.append("- Pre-specified PRIMARY endpoint(s):")
        for outcome in packet.get("primary_outcome_definitions", []):
            tf = outcome.get("time_frame") or "n/a"
            lines.append(f"    - {outcome.get('measure')} (time frame: {tf})")
        lines.append(f"- Current draft: proposed_outcome={packet.get('proposed_outcome')}, "
                     f"confidence={packet.get('confidence')}, review_status={packet.get('review_status')}")
        lines.append(f"- Draft rationale: {packet.get('adjudication_rationale')}")
        lines.append("- Draft evidence:")
        for source in packet.get("evidence_sources", []):
            lines.append(
                f"    - [{source.get('source_type')}] {source.get('title')} "
                f"({source.get('publication_date')}): {source.get('url')}"
            )
            lines.append(f"      - {source.get('evidence_note')}")
        if not packet.get("evidence_sources"):
            lines.append("    - None")
        lines.append("- Draft conflicts:")
        for conflict in packet.get("conflicts", []):
            lines.append(f"    - {conflict.get('description')}")
        if not packet.get("conflicts"):
            lines.append("    - None")
        lines.append("- [ ] Reviewer proposed_outcome: ______  confidence: ______")
        lines.append("- [ ] Evidence sources (type / title / URL / date):")
        lines.append("- [ ] Conflicts observed:")
        lines.append("")

    out_path = args.out or os.path.join(args.output, "review-batch.md")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"export-review-batch failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote review worksheet: {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and verify first-pass adjudication packets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_p = subparsers.add_parser("create-packets", help="Build packets from frozen snapshots")
    create_p.add_argument("--authoritative", required=True, help="Authoritative cohort dir (read-only)")
    create_p.add_argument("--output", required=True, help="Output dir (outside Git)")
    create_p.add_argument("--reviewer", default="claude-first-pass-draft", help="Reviewer identifier")
    create_p.add_argument("--reviewed-at", default=None, help="Fixed ISO-8601 timestamp for reproducibility")
    create_p.add_argument("--nct", action="append", help="NCT ID to include (repeatable; default: batch one)")
    create_p.add_argument("--force", action="store_true", help="Transactionally replace an existing batch")
    create_p.set_defaults(func=cmd_create_packets)

    verify_p = subparsers.add_parser("verify", help="Verify a batch")
    verify_p.add_argument("--output", required=True, help="Batch dir")
    verify_p.add_argument("--authoritative", required=True, help="Authoritative cohort dir")
    verify_p.set_defaults(func=cmd_verify)

    apply_p = subparsers.add_parser(
        "apply-reviews",
        help="Apply review-only JSONL patches and reseal the batch",
    )
    apply_p.add_argument("--authoritative", required=True, help="Authoritative cohort dir")
    apply_p.add_argument("--output", required=True, help="Existing batch dir")
    apply_p.add_argument("--reviews", required=True, help="Review-patch JSONL path")
    apply_p.add_argument(
        "--generated-at",
        default=None,
        help="Fixed ISO-8601 generation timestamp",
    )
    apply_p.add_argument(
        "--force",
        action="store_true",
        help="Required: transactionally replace the existing batch",
    )
    apply_p.set_defaults(func=cmd_apply_reviews)

    summary_p = subparsers.add_parser("summary", help="Summarize a batch")
    summary_p.add_argument("--output", required=True, help="Batch dir")
    summary_p.set_defaults(func=cmd_summary)

    export_p = subparsers.add_parser("export-review-batch", help="Write a second-review worksheet")
    export_p.add_argument("--output", required=True, help="Batch dir")
    export_p.add_argument("--out", default=None, help="Worksheet path (default: <output>/review-batch.md)")
    export_p.set_defaults(func=cmd_export_review_batch)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
