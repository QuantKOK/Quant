# MacroEdge

MacroEdge is becoming a macro event-contract research and risk system for
evaluating prediction-market mispricings around CPI, unemployment, Fed
decisions, GDP, recession indicators, and other financial/macro events.

The goal is not to build a betting bot or turn a small bankroll into a large
return quickly. The goal is to build a disciplined, auditable track record:
estimate fair probabilities, compare them to market-implied probabilities,
trade only when the edge clears strict risk rules, and measure calibration over
50-100 logged decisions.

Suggested bankroll frame:

- Total capital reference: `$1,500`
- Active bankroll: `$300-$500`
- Risk per trade: `$10-$25`
- Max exposure to one event: about `$50`
- Minimum edge: `8-10` percentage points after fees/spreads

See `docs/macroedge_operating_plan.md` for the operating rules.

## Quickstart

MacroEdge is **standard-library only** — no third-party runtime dependencies. To
see the whole thing work end to end, run the offline demo:

```
py -3 demo.py
```

It drives the real CLIs on the bundled example fixtures through the full
lifecycle — contract observation -> trade candidate -> settlement/post-mortem ->
performance reconciliation (with calibration buckets) -> static HTML dashboard —
writing every artifact to a throwaway temp directory (nothing is written into the
repo) and printing the dashboard path at the end. It is offline only: no network,
no credentials, no order placement.

Set up a virtualenv and run the test suite (only `pytest` is needed):

```
.\run.ps1 -CreateVenv -Install -Test      # Windows PowerShell
.\run.ps1 -RunExample                      # runs demo.py
```

## Current code

The supported system lives in `macroedge/`. Retired pre-pivot trading utilities
have been moved to `legacy/` (they need pandas/numpy and are not used by
MacroEdge); the prior Biotech Risk Scout code remains under
`biotech-risk-scout/`.

### Market-contract observations

`macroedge/contracts.py` validates raw macro event-contract observations before
any trade thesis exists. A contract record stores the event type, question,
settlement source/rules, observed bid/ask/last prices, midpoint-implied
probability, spread, URL, timestamp, and a deterministic `contract_hash`.

This layer is deliberately separate from the trade journal: most observed
contracts should never become trade candidates.

`macroedge/adapters/kalshi.py` converts offline Kalshi-style market JSON into
the same platform-neutral contract draft shape. It uses only local JSON and does
not call Kalshi APIs, require credentials, or place trades. Adapter fixtures live
in `macroedge/examples/kalshi-market-*.example.json`.

The Kalshi adapter is intentionally conservative: `event_type` must be supplied
explicitly, `close_time` is retained only as trading-close metadata and is never
used as event or settlement time, settlement timing prefers `expiration_time`
then `expected_expiration_time` then `latest_expiration_time`, and endpoint
prices (`0.00` / `1.00`) are treated as absent quotes rather than clamped.

Use `macroedge/app/kalshi.py` when the input is a raw offline Kalshi-style market
fixture and you want the adapter to build the neutral MacroEdge observation:

```bash
# Validate a raw Kalshi fixture as a MacroEdge contract observation
py -3 macroedge/app/kalshi.py validate \
  --input macroedge/examples/kalshi-market-cpi.example.json \
  --event-type cpi \
  --observed-at 2026-07-14T20:00:00-05:00 \
  --observation-id kalshi-cpi-example

# Emit the canonical observation JSON for audit/reference
py -3 macroedge/app/kalshi.py emit \
  --input macroedge/examples/kalshi-market-cpi.example.json \
  --output macroedge/contract-observation-kalshi.example.json \
  --event-type cpi \
  --observed-at 2026-07-14T20:00:00-05:00 \
  --observation-id kalshi-cpi-example

# Append the adapted observation to the local market tape
py -3 macroedge/app/kalshi.py append \
  --input macroedge/examples/kalshi-market-cpi.example.json \
  --ledger macroedge/contract-observations.jsonl \
  --event-type cpi \
  --observed-at 2026-07-14T20:00:00-05:00 \
  --observation-id kalshi-cpi-example

# Append a whole offline fixture directory to the local market tape
py -3 macroedge/app/kalshi.py batch-append \
  --input-dir macroedge/examples \
  --glob "kalshi-market-*.example.json" \
  --ledger macroedge/contract-observations.jsonl \
  --observed-at 2026-07-14T20:00:00-05:00 \
  --id-prefix kalshi-snapshot-20260714
```

These commands are fixture-to-ledger tools only: no Kalshi API call, no
credentials, and no order execution.

```bash
# Validate a contract draft and print its contract_hash
py -3 macroedge/app/contracts.py validate \
  --input macroedge/examples/contract-draft.example.json \
  --observation-id example-contract-observation \
  --observed-at 2026-07-14T20:00:00-05:00

# Write the canonical observation JSON for audit/reference
py -3 macroedge/app/contracts.py emit \
  --input macroedge/examples/contract-draft.example.json \
  --output macroedge/contract-observation.example.json \
  --observation-id example-contract-observation \
  --observed-at 2026-07-14T20:00:00-05:00

# Re-verify an emitted observation record later
py -3 macroedge/app/contracts.py verify \
  --input macroedge/contract-observation.example.json
```

Pass both `--observation-id` and `--observed-at` when you need a reproducible
`contract_hash`; otherwise a fresh observation ID is generated.

```bash
# Append observations to a tamper-evident local market tape
py -3 macroedge/app/contracts.py append \
  --input macroedge/examples/contract-draft.example.json \
  --ledger macroedge/contract-observations.jsonl \
  --observation-id example-contract-observation \
  --observed-at 2026-07-14T20:00:00-05:00

# Verify the observation ledger and print its current head
py -3 macroedge/app/contracts.py verify-ledger --ledger macroedge/contract-observations.jsonl
py -3 macroedge/app/contracts.py summary --ledger macroedge/contract-observations.jsonl
py -3 macroedge/app/contracts.py head --ledger macroedge/contract-observations.jsonl
```

### Trade journal (append-only, offline)

`macroedge/app/journal.py` is a CLI for a tamper-evident, hash-chained journal of
macro event-contract trade *candidates*. It is a probability-research journal,
not a betting bot: it never contacts a market/API and never places trades. Each
candidate is validated by `macroedge.journal.build_trade_candidate` (edge, risk,
and exposure guardrails) and appended to a JSONL ledger with predecessor-hash
chaining.

Trade candidates may optionally include `contract_observation` with the source
contract observation's `observation_id`, `contract_hash`, and `observed_at`.
Manual candidates remain valid without this reference, but linked candidates
carry cleaner audit lineage from observed market to thesis to journal entry.

For candidate edge math, `entry_price` and `fair_probability` must both be
expressed for the selected `side` (`YES` or `NO`). The stored edge is gross of
fees, spread, and slippage; the operating plan still requires a real-world
8-10 percentage-point edge after those costs.

```bash
# Seed a candidate draft from a verified contract observation
py -3 macroedge/app/journal.py draft-from-observation \
  --input macroedge/contract-observation-kalshi.example.json \
  --output macroedge/trade-draft-from-observation.example.json \
  --side YES \
  --fair-probability 0.53 \
  --thesis-summary "Manual thesis from cited macro sources; not an automated recommendation." \
  --data-source https://www.bls.gov/cpi/ \
  --active-bankroll-usd 400 \
  --planned-risk-usd 20 \
  --created-at 2026-07-15T02:00:00+00:00 \
  --candidate-id example-candidate

# Validate a draft (see macroedge/examples/trade-draft.example.json)
py -3 macroedge/app/journal.py validate \
  --input macroedge/examples/trade-draft.example.json \
  --created-at 2026-07-15T02:00:00+00:00

# Append a validated candidate to an append-only ledger
py -3 macroedge/app/journal.py append \
  --input macroedge/examples/trade-draft.example.json \
  --ledger macroedge/ledger.jsonl \
  --created-at 2026-07-15T02:00:00+00:00

# Verify the whole chain (hashes, previous_hash links, ids, ordering, edge/risk)
py -3 macroedge/app/journal.py verify --ledger macroedge/ledger.jsonl

# Summarize candidate count, risk, edge, event mix, side mix, and post-mortem status
py -3 macroedge/app/journal.py summary --ledger macroedge/ledger.jsonl

# Append a settlement/post-mortem record without rewriting the candidate journal
py -3 macroedge/app/journal.py settle \
  --journal-ledger macroedge/ledger.jsonl \
  --settlement-ledger macroedge/settlements.jsonl \
  --candidate-id example-candidate \
  --actual-result YES \
  --settled-at 2026-07-15T12:00:00+00:00 \
  --notes "Resolved from the cited official source."

# Verify and summarize the settlement ledger
py -3 macroedge/app/journal.py verify-settlements --ledger macroedge/settlements.jsonl
py -3 macroedge/app/journal.py settlement-summary --ledger macroedge/settlements.jsonl

# Reconcile candidates vs settlements into a performance scorecard
py -3 macroedge/app/journal.py performance \
  --journal-ledger macroedge/ledger.jsonl \
  --settlement-ledger macroedge/settlements.jsonl

# Export the same scorecard for dashboards/reports
py -3 macroedge/app/journal.py performance \
  --journal-ledger macroedge/ledger.jsonl \
  --settlement-ledger macroedge/settlements.jsonl \
  --output macroedge/performance-summary.csv \
  --format csv

# Render a portable static dashboard from either the JSON or CSV export
py -3 macroedge/app/journal.py performance-dashboard \
  --input macroedge/performance-summary.csv \
  --output macroedge/performance-dashboard.html

# Print the current ledger head hash
py -3 macroedge/app/journal.py head --ledger macroedge/ledger.jsonl
```

`validate`/`append` accept optional `--created-at <ISO-8601>` and
`--candidate-id` for reproducible entries. `verify` reports every issue it finds:
invalid JSON, `ledger_hash`/content mismatch, `previous_hash` break, duplicate
`candidate_id`, non-monotonic `created_at`, and schema/risk/edge violations.
`settle` appends a separate post-mortem record instead of rewriting the original
candidate. `performance` verifies both ledgers first, then reports
settled/unsettled candidates, win/loss/void counts, win rate, average Brier
score, planned risk, edge averages, event/side mixes, and calibration buckets
that compare fair probabilities against actual non-void outcomes. Add `--output
<path> --format json|csv` to write the same scorecard as a durable artifact.
`performance-dashboard` renders either export format into a self-contained local
HTML dashboard for review; it has no network/runtime dependency and is still
research-only.

## Legacy Quant

Files:
- `strategy.py` - core functions: `vwap`, `make_trend_following`, `make_stat_arb`, `simulate_option_proxy`.
- `M1` - example runner (run `python M1` to see example output).
- `tests/` - pytest tests.

Quick start (Windows PowerShell):
```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q
```

CI: GitHub Actions workflow is configured in `.github/workflows/pytest.yml` to run tests on push/PR to `main`.
