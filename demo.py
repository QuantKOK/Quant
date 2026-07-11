#!/usr/bin/env python
"""End-to-end MacroEdge demo — runs the full offline research lifecycle.

This is the single "does it work?" entry point. It drives the real CLIs
(``macroedge.app.contracts`` and ``macroedge.app.journal``) in-process, using
the bundled example fixtures, and writes every artifact to a throwaway system
temp directory. Nothing is written into the repository.

Pipeline demonstrated:

    contract draft -> emit + append observation (hash-chained ledger)
                   -> seed a trade-candidate draft from the observation
                   -> append candidate to the tamper-evident journal
                   -> settle the candidate (post-mortem, append-only)
                   -> reconcile performance (with calibration buckets)
                   -> render a static HTML dashboard

It is offline only: no network, no credentials, no order placement. Run it with:

    py -3 demo.py

At the end it prints the temp directory and the path to the rendered dashboard.
"""

from __future__ import annotations

import os
import sys
import tempfile

# Make the repo importable whether run from the repo root or elsewhere.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Best-effort UTF-8 console so summary glyphs never crash a Windows terminal.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - older interpreters / redirected stdout
    pass

from macroedge.app import contracts as contracts_cli  # noqa: E402
from macroedge.app import journal as journal_cli  # noqa: E402

EXAMPLES = os.path.join(REPO_ROOT, "macroedge", "examples")
CONTRACT_DRAFT = os.path.join(EXAMPLES, "contract-draft.example.json")

# Fixed inputs -> reproducible hashes/output across runs.
OBSERVED_AT = "2026-07-14T20:00:00-05:00"
OBSERVATION_ID = "demo-cpi-observation"
CREATED_AT = "2026-07-15T02:00:00+00:00"
SETTLED_AT = "2026-07-15T12:00:00-04:00"
RECORDED_AT = "2026-07-16T02:00:00+00:00"
CANDIDATE_ID = "demo-candidate"
SETTLEMENT_ID = "demo-settlement"


def _step(number: int, title: str, cli, argv: list[str]) -> None:
    """Run one CLI subcommand in-process; abort the demo on any failure."""
    print(f"\n{'=' * 72}\n[{number}] {title}\n{'-' * 72}")
    print("$ " + _display_command(cli, argv))
    rc = cli.main(argv)
    if rc != 0:
        print(f"\nDemo aborted: step {number} exited with code {rc}.", file=sys.stderr)
        raise SystemExit(1)


def _display_command(cli, argv: list[str]) -> str:
    module = "contracts" if cli is contracts_cli else "journal"
    return " ".join([f"py -3 -m macroedge.app.{module}", *argv])


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="macroedge-demo-")
    observation = os.path.join(workdir, "contract-observation.json")
    obs_ledger = os.path.join(workdir, "contract-observations.jsonl")
    candidate_draft = os.path.join(workdir, "trade-candidate-draft.json")
    journal_ledger = os.path.join(workdir, "journal.jsonl")
    settlement_ledger = os.path.join(workdir, "settlements.jsonl")
    performance = os.path.join(workdir, "performance-summary.json")
    dashboard = os.path.join(workdir, "performance-dashboard.html")

    print("MacroEdge end-to-end demo (offline; no network, no credentials, no trading).")
    print(f"Working directory (throwaway, outside the repo):\n  {workdir}")

    _step(1, "Emit a canonical contract observation from the example draft",
          contracts_cli,
          ["emit", "--input", CONTRACT_DRAFT, "--output", observation,
           "--observed-at", OBSERVED_AT, "--observation-id", OBSERVATION_ID])

    _step(2, "Append the observation to a hash-chained observation ledger",
          contracts_cli,
          ["append", "--input", CONTRACT_DRAFT, "--ledger", obs_ledger,
           "--observed-at", OBSERVED_AT, "--observation-id", OBSERVATION_ID])

    _step(3, "Verify the observation ledger",
          contracts_cli, ["verify-ledger", "--ledger", obs_ledger])

    _step(4, "Seed a trade-candidate draft from the observation (manual thesis)",
          journal_cli,
          ["draft-from-observation", "--input", observation, "--output", candidate_draft,
           "--side", "YES", "--fair-probability", "0.53",
           "--thesis-summary", "Manual analyst thesis for demonstration only.",
           "--data-source", "https://www.bls.gov/cpi/",
           "--active-bankroll-usd", "400", "--planned-risk-usd", "20",
           "--candidate-id", CANDIDATE_ID, "--created-at", CREATED_AT])

    _step(5, "Append the candidate to the tamper-evident journal",
          journal_cli,
          ["append", "--input", candidate_draft, "--ledger", journal_ledger,
           "--candidate-id", CANDIDATE_ID, "--created-at", CREATED_AT])

    _step(6, "Verify the candidate journal", journal_cli,
          ["verify", "--ledger", journal_ledger])

    _step(7, "Summarize the candidate journal", journal_cli,
          ["summary", "--ledger", journal_ledger])

    _step(8, "Settle the candidate (append-only post-mortem, actual result = YES)",
          journal_cli,
          ["settle", "--journal-ledger", journal_ledger,
           "--settlement-ledger", settlement_ledger, "--candidate-id", CANDIDATE_ID,
           "--actual-result", "YES", "--settled-at", SETTLED_AT,
           "--recorded-at", RECORDED_AT, "--settlement-id", SETTLEMENT_ID,
           "--notes", "Resolved YES per official source (demo)."])

    _step(9, "Verify the settlement ledger", journal_cli,
          ["verify-settlements", "--ledger", settlement_ledger])

    _step(10, "Summarize the settlement ledger", journal_cli,
          ["settlement-summary", "--ledger", settlement_ledger])

    _step(11, "Reconcile performance to the console (edge, Brier, calibration buckets)",
          journal_cli,
          ["performance", "--journal-ledger", journal_ledger,
           "--settlement-ledger", settlement_ledger])

    _step(12, "Export the same performance summary to JSON",
          journal_cli,
          ["performance", "--journal-ledger", journal_ledger,
           "--settlement-ledger", settlement_ledger,
           "--output", performance, "--format", "json"])

    _step(13, "Render the static HTML performance dashboard",
          journal_cli,
          ["performance-dashboard", "--input", performance, "--output", dashboard])

    print(f"\n{'=' * 72}\nDemo complete. All steps passed.\n{'-' * 72}")
    print("Artifacts (in the throwaway temp dir above):")
    for label, path in (
        ("observation", observation),
        ("observation ledger", obs_ledger),
        ("candidate draft", candidate_draft),
        ("journal ledger", journal_ledger),
        ("settlement ledger", settlement_ledger),
        ("performance summary", performance),
        ("dashboard (open in a browser)", dashboard),
    ):
        print(f"  {label:32} {path}")
    print("\nNothing was written into the repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
