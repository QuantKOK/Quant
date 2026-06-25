# Biotech Risk Scout

This project is an early prototype of an AI Special Situations Research Agent focused on small-cap biotechnology companies. It aims to surface tickers with upcoming catalysts, analyze cash runway and potential dilution risk, and generate concise research cards summarizing why a situation is interesting and how it could go wrong.

## Current Status

The SEC filings layer uses the SEC-maintained EDGAR company-submissions JSON endpoint. It can resolve a ticker to CIK, fetch recent filing metadata, identify the latest 10-Q, 10-K, and 8-K, and flag recent financing-related forms such as S-1, S-3, 424B filings, and FWP filings.

The SEC layer also uses the SEC company-facts XBRL endpoint for a first-pass cash runway estimate. It pulls latest reported cash and the latest operating cash-flow duration fact, estimates monthly burn when operating cash flow is negative, and calculates runway months when enough data is available.

The ClinicalTrials.gov layer uses the ClinicalTrials.gov API v2 studies endpoint. It can search by sponsor/company name, map selected tickers to sponsor names, pull study metadata, estimate the nearest active primary-completion catalyst, and return trial details for research cards.

The research card output is now structured like a diligence card, with separate catalyst, financial runway, SEC filings, risk read, and data-note sections.

The cash-runway logic is still a first-pass heuristic. It should be reviewed against actual filings before being used for serious diligence.

## Layout

```txt
biotech-risk-scout/
├── app/               # CLI entry point
├── scout/
│   ├── ingest/        # Data ingestion modules
│   └── reports/       # Report and research card abstractions
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

Run the CLI with a real ticker:

```bash
python biotech-risk-scout/app/main.py MRNA
```

The output includes company match, catalyst summary, trial count, cash, operating cash flow, monthly burn, runway months, dilution risk, latest SEC filings, main risk, and next diligence steps.

## Tests

Run offline unit tests with:

```bash
python -m pytest -q biotech-risk-scout/tests
```

The current tests validate the first-pass SEC company-facts cash runway calculation and the research card output without depending on live SEC requests.

## Next Engineering Step

Improve ticker-to-sponsor mapping by using the SEC company name as a fallback for ClinicalTrials.gov sponsor searches, then add more fixtures around the SEC company-facts cash runway calculations.
