# Biotech Risk Scout

This project is an early prototype of an AI Special Situations Research Agent focused on small-cap biotechnology companies. It aims to surface tickers with upcoming catalysts, analyze cash runway and potential dilution risk, and generate concise research cards summarizing why a situation is interesting and how it could go wrong.

## Current Status

The SEC filings layer uses the SEC-maintained EDGAR company-submissions JSON endpoint. It can resolve a ticker to CIK, fetch recent filing metadata, identify the latest 10-Q, 10-K, and 8-K, and flag recent financing-related forms such as S-1, S-3, 424B filings, and FWP filings.

The SEC layer also uses the SEC company-facts XBRL endpoint for a first-pass cash runway estimate. It pulls latest reported cash and the latest operating cash-flow duration fact, estimates monthly burn when operating cash flow is negative, and calculates runway months when enough data is available.

The ClinicalTrials.gov layer uses the ClinicalTrials.gov API v2 studies endpoint. It can search by sponsor/company name, map selected tickers to sponsor names, pull study metadata, estimate the nearest active primary-completion catalyst, and return trial details for research cards.

The research card output is now structured like a diligence card, with separate catalyst, financial runway, SEC filings, risk read, and data-note sections.

The scanner now ranks multiple tickers with a 0-100 research-priority score. The score is a diligence-queue tool only, not an investment recommendation.

The scanner can export ranked results to CSV and JSON for saved watchlists, spreadsheet review, and future score-change tracking. It also supports watchlist files through `--tickers-file`, dated scan snapshots through `--snapshot-dir`, snapshot comparisons through `--compare-snapshots`, markdown alert reports through `--alert-report`, and per-ticker score explanations through `--explain`.

A local daily-scan runner and weekday GitHub Actions schedule can run the default watchlist, restore the previous cached snapshot, save a new snapshot cache, generate alert reports when a prior snapshot exists, and upload snapshot artifacts.

The cash-runway logic is still a first-pass heuristic. It should be reviewed against actual filings before being used for serious diligence.

## Layout

```txt
biotech-risk-scout/
├── app/               # CLI entry points
├── scripts/           # Local automation scripts
├── watchlists/        # Watchlist inputs
├── scout/
│   ├── ingest/        # Data ingestion modules
│   ├── reports/       # Report and research card abstractions
│   ├── scoring/       # Research-priority scoring
│   └── storage/       # Snapshot/output storage helpers
└── README.md
```

## SEC EDGAR Setup

The SEC asks scripted tools to declare a descriptive User-Agent. Before making repeated requests, set an environment variable with your name or app name and contact email.

PowerShell:

```powershell
$env:SEC_USER_AGENT="BiotechRiskScout kaiveonday@gmail.com"
```

macOS/Linux:

```bash
export SEC_USER_AGENT="BiotechRiskScout kaiveonday@gmail.com"
```

## CLI

Generate a single-ticker card:

```bash
python biotech-risk-scout/app/main.py MRNA
```

Scan and rank multiple tickers:

```bash
python biotech-risk-scout/app/scan.py MRNA VKTX SAVA PRAX CRSP
```

Print score explanations after scanning:

```bash
python biotech-risk-scout/app/scan.py MRNA VKTX SAVA --explain
```

The explanation shows each ticker's 0-100 score, component breakdown, main reason, red flags, and key inputs used.

Scan from a watchlist file:

```bash
python biotech-risk-scout/app/scan.py --tickers-file biotech-watchlist.txt
```

Watchlist files can use newlines, commas, spaces, blank lines, and `#` comments.

Export scan results:

```bash
python biotech-risk-scout/app/scan.py MRNA VKTX SAVA PRAX CRSP --json scan.json
python biotech-risk-scout/app/scan.py MRNA VKTX SAVA PRAX CRSP --csv scan.csv
python biotech-risk-scout/app/scan.py --tickers-file biotech-watchlist.txt --csv scan.csv --json scan.json --no-table
```

Save a dated scan snapshot:

```bash
python biotech-risk-scout/app/scan.py --tickers-file biotech-watchlist.txt --snapshot-dir snapshots --no-table
```

This writes both `snapshots/scan-YYYYMMDD.json` and `snapshots/latest.json`.

Compare two snapshots:

```bash
python biotech-risk-scout/app/scan.py --compare-snapshots snapshots/scan-20260624.json snapshots/scan-20260625.json
python biotech-risk-scout/app/scan.py --compare-snapshots snapshots/scan-20260624.json snapshots/scan-20260625.json --compare-json comparison.json
```

Snapshot comparison reports added tickers, removed tickers, score changes, rank changes, catalyst/date changes, runway changes, dilution changes, and evidence-quality changes.

Create a markdown alert report from a comparison:

```bash
python biotech-risk-scout/app/scan.py --compare-snapshots snapshots/scan-20260624.json snapshots/scan-20260625.json --alert-report alerts/latest-alerts.md
```

The alert report summarizes biggest score moves, new names surfaced, removed names, and names that need manual inspection.

Run the local daily watchlist scan:

```bash
python biotech-risk-scout/scripts/run_daily_scan.py --no-table
```

The default watchlist lives at `biotech-risk-scout/watchlists/biotech-watchlist.txt`. The script writes CSV, JSON record exports, `scan-YYYYMMDD.json`, `latest.json`, and `latest-alerts.md` into `biotech-risk-scout/snapshots` by default. When a previous `latest.json` exists, it also writes `previous-latest.json` and `latest-comparison.json`.

The scanner prints rank, ticker, score, catalyst, days, runway, dilution risk, evidence quality, trial count, main reason, and main risk.

Exports include ticker, company name, score, reason, red flags, catalyst, days, trial counts, evidence quality, cash, burn, runway, dilution risk, financing flags, and latest filing dates.

## Tests

Run offline unit tests with:

```bash
python -m pytest -q biotech-risk-scout/tests
```

The current tests validate the first-pass SEC company-facts cash runway calculation, the research card output, the research-priority scoring rubric, scanner CSV/JSON exports, ticker-file parsing, scan snapshot writing, snapshot comparison, markdown alert reports, daily-scan alert artifact helpers, and score explanation formatting without depending on live SEC requests.

## GitHub Actions

The smoke workflow compiles the project and runs all offline tests on pushes touching Biotech Risk Scout. It also supports manual runs and a weekday scheduled scan that restores the previous `biotech-risk-scout/snapshots` cache, runs the daily scanner, saves the new cache, and uploads snapshot, CSV, JSON, comparison, and alert-report artifacts.

## Next Engineering Step

Add sponsor fallback for ClinicalTrials.gov so the scanner can search by SEC company name when a ticker is not in the manual sponsor map.
