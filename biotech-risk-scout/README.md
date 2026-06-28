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
python biotech-risk-scout/app/scan.py MRNA VKTX FLNA PRAX CRSP
```

Print score explanations after scanning:

```bash
python biotech-risk-scout/app/scan.py MRNA VKTX FLNA --explain
```

The explanation shows each ticker's 0-100 score, component breakdown, main reason, red flags, and key inputs used.

Scan from a watchlist file:

```bash
python biotech-risk-scout/app/scan.py --tickers-file biotech-watchlist.txt
```

Watchlist files can use newlines, commas, spaces, blank lines, and `#` comments.

Export scan results:

```bash
python biotech-risk-scout/app/scan.py MRNA VKTX FLNA PRAX CRSP --json scan.json
python biotech-risk-scout/app/scan.py MRNA VKTX FLNA PRAX CRSP --csv scan.csv
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

The alert report opens with an **Operator Brief** — a plain-English daily summary listing the scan date, counts of new/removed/score-changed names, how many names have validated SEC filing-text flags, the highest-priority name, and the biggest positive and negative score moves. Below the brief it details biggest score moves, new names surfaced, removed names, names that need manual inspection, and validated SEC filing-text flags when present. The brief is diligence triage only, not investment advice.

Run the local daily watchlist scan:

```bash
python biotech-risk-scout/scripts/run_daily_scan.py --no-table
```

`SEC_USER_AGENT` must be set before running the daily scanner; see **SEC EDGAR Setup** above. The runner exits with a clear error when it is missing.

Run the daily scan with capped SEC filing-text validation and the persistent cache:

```bash
python biotech-risk-scout/scripts/run_daily_scan.py --no-table --validate-sec-text --max-sec-documents 2 --max-workers 4 --sec-validation-cache-ttl-days 30
```

`--sec-validation-cache-ttl-days` (default 30) sets how long a cached validation result stays fresh before it is refetched; see **Optional Filing-Document Text Fetch** below for cache TTL and pruning details.

The default watchlist lives at `biotech-risk-scout/watchlists/biotech-watchlist.txt`. The script writes CSV, JSON record exports, `scan-YYYYMMDD.json`, `latest.json`, and `latest-alerts.md` into `biotech-risk-scout/snapshots` by default. When a previous `latest.json` exists, it also writes `previous-latest.json` and `latest-comparison.json`.

If every ticker scan fails, the runner exits nonzero without replacing existing outputs. Partial failures are reported to stderr while successful ticker results continue through exports and snapshots.

### Alert report delivery (local only)

After `latest-alerts.md` is generated, the daily runner can deliver a copy through safe, local-only channels. These options only print, copy files, or generate a digest file on the local machine and never contact an external service. An opt-in Discord webhook channel is documented separately in **Discord webhook delivery** below; it is the only channel that makes a network call, and only when you explicitly pass a Discord flag.

```bash
# Print the alert report to stdout
python biotech-risk-scout/scripts/run_daily_scan.py --no-table --print-alert-report

# Archive a copy of the alert report into a folder (keeps the same filename)
python biotech-risk-scout/scripts/run_daily_scan.py --no-table --archive-alert-report-dir biotech-risk-scout/alerts-archive

# Write an email-style digest file (subject + body); no email is sent
python biotech-risk-scout/scripts/run_daily_scan.py --no-table --write-email-digest biotech-risk-scout/alerts-archive/digest.txt
```

Delivery runs only after a successful scan and report generation. Each delivery action is best-effort: if one fails it prints a warning to stderr but does not change the daily-scan exit code (the scan still fails only when every ticker scan failed). The delivery abstraction lives in `scout/delivery/` (`ConsoleDelivery`, `FileArchiveDelivery`, `build_email_digest`, and `DiscordWebhookDelivery`), built on a shared `AlertDelivery` interface so future channels (email, Slack, GitHub Issues) can be added without changing callers. The email digest preserves the markdown report and prepends a diligence-only header; it never sends anything.

### Discord webhook delivery (opt-in)

The daily runner can post a compact alert summary to a Discord channel via an incoming webhook. This is **opt-in** and makes a network call only when you pass a Discord flag — default scans never contact Discord.

```bash
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." \
  python biotech-risk-scout/scripts/run_daily_scan.py --validate-sec-text --discord-webhook-env DISCORD_WEBHOOK_URL
```

Two ways to supply the webhook:

* `--discord-webhook-env NAME` — read the webhook URL from the environment variable `NAME` (recommended). If the variable is missing or empty, the runner prints a warning and skips Discord delivery.
* `--discord-webhook-url URL` — pass the URL directly (discouraged, since it can land in shell history and process listings).

Notes:

* Store the webhook URL as a secret/environment variable; never commit it. Webhook URLs are not hardcoded anywhere in the project.
* No Discord message is sent unless a Discord flag is passed.
* The posted message contains a short header, the Operator Brief (if present), and a diligence-only disclaimer. It is truncated to stay under Discord's 2000-character limit; the full report remains in `latest-alerts.md` and the uploaded artifacts.
* The webhook URL is never written to logs, results, or error messages — failure messages redact it.
* A Discord network failure returns a failed `DeliveryResult` and prints a warning. Add `--require-delivery` when delivery failure should make the daily scan exit nonzero.

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

The cache is human-readable JSON. Each entry records `matched_flags`, the `cached_at` ISO timestamp, and the `source_url`:

```json
{
  "version": 1,
  "documents": {
    "https://www.sec.gov/Archives/edgar/data/.../doc.htm": {
      "cached_at": "2026-06-27T12:00:00+00:00",
      "matched_flags": ["has_going_concern"],
      "source_url": "https://www.sec.gov/Archives/edgar/data/.../doc.htm"
    }
  }
}
```

**Cache TTL and pruning.** Cached entries are reused only while they are fresh. The default time-to-live is 30 days; control it with `--sec-validation-cache-ttl-days N` on the daily runner. A stale entry — or a legacy entry written before TTL support that has no `cached_at` — is treated as stale, refetched, and overwritten with a fresh entry. After a validated daily scan, the runner prunes stale entries from the cache and reports how many were removed. Pruning is also available programmatically via `prune_validation_cache(cache_path, ttl_days=30)`, which returns `{"before": X, "after": Y, "removed": Z}` and is safe on a missing or corrupt cache (it returns zero counts rather than raising).

## Tests

Run offline unit tests with:

```bash
python -m pytest -q biotech-risk-scout/tests
```

The current tests validate the first-pass SEC company-facts cash runway calculation, SEC financing/structural flag classification, the research card output, the research-priority scoring rubric, scanner CSV/JSON exports, ticker-file parsing, scan snapshot writing, snapshot comparison, markdown alert reports, daily-scan alert artifact helpers, local alert-report delivery (console, file archive, and email-digest generation), Discord webhook delivery (message building, payload POST, URL redaction, and env-based opt-in — all with mocked network), SEC validation cache TTL freshness and pruning, score explanation formatting, and ClinicalTrials.gov sponsor fallback without depending on live SEC or Discord requests.

## GitHub Actions

The smoke workflow compiles the project and runs all offline tests on pushes touching Biotech Risk Scout. It also supports manual runs and a weekday scheduled scan that restores previous snapshots and SEC validation results, runs the daily scanner with validation capped at two selected documents per ticker, saves the updated caches, and uploads snapshot, CSV, JSON, comparison, and alert-report artifacts.

Scheduled scans require a GitHub Actions repository secret named `SEC_USER_AGENT`. Set it to a descriptive application name plus a monitored contact email, following the SEC guidance above. The workflow fails early with a clear error when the secret is missing rather than sending requests with an anonymous placeholder.

Discord delivery is optional and controlled by the `DISCORD_WEBHOOK_URL` repository secret. When present, scheduled and manually dispatched scans deliver the Operator Brief to Discord and require delivery success. When absent, the workflow logs a notice, skips Discord, and still produces all scan artifacts. The secret value is passed only through the environment and is never printed.

## Next Engineering Step

Add delivery observability and duplicate-notification suppression.
