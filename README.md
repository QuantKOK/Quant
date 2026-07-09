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

## Current code

The new MacroEdge foundation lives in `macroedge/`. The original quant utility
files and the prior Biotech Risk Scout code remain in place while the project
pivots.

### Market-contract observations

`macroedge/contracts.py` validates raw macro event-contract observations before
any trade thesis exists. A contract record stores the event type, question,
settlement source/rules, observed bid/ask/last prices, midpoint-implied
probability, spread, URL, timestamp, and a deterministic `contract_hash`.

This layer is deliberately separate from the trade journal: most observed
contracts should never become trade candidates.

### Trade journal (append-only, offline)

`macroedge/app/journal.py` is a CLI for a tamper-evident, hash-chained journal of
macro event-contract trade *candidates*. It is a probability-research journal,
not a betting bot: it never contacts a market/API and never places trades. Each
candidate is validated by `macroedge.journal.build_trade_candidate` (edge, risk,
and exposure guardrails) and appended to a JSONL ledger with predecessor-hash
chaining.

```bash
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

# Print the current ledger head hash
py -3 macroedge/app/journal.py head --ledger macroedge/ledger.jsonl
```

`validate`/`append` accept optional `--created-at <ISO-8601>` and
`--candidate-id` for reproducible entries. `verify` reports every issue it finds:
invalid JSON, `ledger_hash`/content mismatch, `previous_hash` break, duplicate
`candidate_id`, non-monotonic `created_at`, and schema/risk/edge violations.

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
