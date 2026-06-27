# Biotech Risk Scout

This project is an early prototype of an AI Special Situations Research Agent focused on small-cap biotechnology companies. It aims to surface tickers with upcoming catalysts, analyze cash runway and potential dilution risk, and generate concise research cards summarizing why a situation is interesting and how it could go wrong.

## Current Status

The SEC filings layer uses the SEC-maintained EDGAR company-submissions JSON endpoint. It can resolve a ticker to CIK, fetch recent filing metadata, identify the latest 10-Q, 10-K, and 8-K, classify recent financing forms into shelf registrations, registration statements, ATM/offering signals, and flag structural-risk hints such as reverse splits, going-concern language, and listing-compliance issues.

The SEC layer also uses the SEC company-facts XBRL endpoint for a first-pass cash runway estimate. It pulls latest reported cash and the latest operating cash-flow duration fact, estimates monthly burn when operating cash flow is negative, and calculates runway months when enough data is available.

The ClinicalTrials.gov layer uses the ClinicalTrials.gov API v2 studies endpoint. It can search by sponsor/company name, map selected tickers to sponsor names, fall back to the SEC company name when a ticker is not manually mapped, pull study metadata, estimate the nearest active primary-completion catalyst, and return trial details for research cards.

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

`SEC_USER_AGENT` must be set before running the daily scanner; see **SEC EDGAR Setup** above. The runner exits with a clear error when it is missing.

Run the daily scan with capped SEC filing-text validation and the persistent cache:

```bash
python biotech-risk-scout/scripts/run_daily_scan.py --no-table --validate-sec-text --max-sec-documents 2 --max-workers 4
```

The default watchlist lives at `biotech-risk-scout/watchlists/biotech-watchlist.txt`. The script writes CSV, JSON record exports, `scan-YYYYMMDD.json`, `latest.json`, and `latest-alerts.md` into `biotech-risk-scout/snapshots` by default. When a previous `latest.json` exists, it also writes `previous-latest.json` and `latest-comparison.json`.

If every ticker scan fails, the runner exits nonzero without replacing existing outputs. Partial failures are reported to stderr while successful ticker results continue through exports and snapshots.

The scanner prints rank, ticker, score, catalyst, days, runway, dilution risk, evidence quality, trial count, main reason, and main risk.

Exports include ticker, company name, score, reason, red flags, catalyst, days, trial counts, evidence quality, cash, burn, runway, dilution risk, financing-category flags, structural red flags, financing-form counts, and latest filing dates.

## SEC Financing Categories

Recent filings are separated into clearer buckets:

```txt
shelf registration: S-3, S-3/A, S-3ASR, S-3ASR/A
registration statement: S-1, S-1/A, POS AM
ATM/offering: 424B2, 424B3, 424B5, FWP, or ATM/sales-agreement keywords
structural red flags: reverse split, going concern, delisting/listing non-compliance keywords
```

These are metadata/keyword heuristics from recent SEC filing rows. They are triage flags, not final conclusions; serious diligence still requires opening and reading the actual filing.

## SEC Filing URLs

All summarized filing objects (`recent_filings`, `latest_10q`, `latest_10k`, `latest_8k`, and all `financing_recent_filings` sub-lists) now include direct SEC EDGAR links when CIK and accession data are available:

* `filing_index_url` — the SEC EDGAR filing index page (lists all documents in the filing)
* `primary_document_url` — direct link to the primary filing document (HTM/HTML)

These URLs can be opened manually in a browser to read the actual filing text without additional tooling.

## Optional Filing-Document Text Fetch

Two optional helpers exist in `scout/ingest/sec_filings.py` for ad-hoc validation:

* `fetch_filing_document_text(url)` — fetches and lightly HTML-strips a filing document from a `primary_document_url`. **Not called during normal scans** to avoid SEC rate-limit impact and performance regression.
* `scan_filing_text_flags(text)` — pure offline function that scans filing text for going-concern, reverse-split, ATM/offering, and listing non-compliance signals. Can be called on any text string without network access.

Default scans do not fetch SEC filing text. Add `--validate-sec-text` to validate only selected high-risk filing groups, with `--max-sec-documents 3` controlling the per-ticker cap. Validation results are heuristic matches for diligence triage, require human review, and are not final conclusions. JSON and snapshots retain the structured results; CSV stores the validation fields as compact JSON strings.

```bash
python biotech-risk-scout/app/scan.py MRNA VKTX --validate-sec-text --max-sec-documents 2 --no-table --json scan-validated.json
```

Validated results are cached by filing-document URL in `biotech-risk-scout/.cache/sec-validation.json`, so later validation scans do not refetch unchanged filings. Use `--sec-validation-cache PATH` to place the cache elsewhere. Cache read/write failures are reported in `sec_validation_errors` and do not crash the scan.

## Tests

Run offline unit tests with:

```bash
python -m pytest -q biotech-risk-scout/tests
```

The current tests validate the first-pass SEC company-facts cash runway calculation, SEC financing/structural flag classification, the research card output, the research-priority scoring rubric, scanner CSV/JSON exports, ticker-file parsing, scan snapshot writing, snapshot comparison, markdown alert reports, daily-scan alert artifact helpers, score explanation formatting, and ClinicalTrials.gov sponsor fallback without depending on live SEC requests.

## GitHub Actions

The smoke workflow compiles the project and runs all offline tests on pushes touching Biotech Risk Scout. It also supports manual runs and a weekday scheduled scan that restores previous snapshots and SEC validation results, runs the daily scanner with validation capped at two selected documents per ticker, saves the updated caches, and uploads snapshot, CSV, JSON, comparison, and alert-report artifacts.

Scheduled scans require a GitHub Actions repository secret named `SEC_USER_AGENT`. Set it to a descriptive application name plus a monitored contact email, following the SEC guidance above. The workflow fails early with a clear error when the secret is missing rather than sending requests with an anonymous placeholder.

## Next Engineering Step

Add an optional notification delivery adapter for generated alert reports.
