# Biotech Risk Scout

This project is an early prototype of an **AI Special Situations Research Agent** focused on small-cap biotechnology companies. It aims to surface tickers with upcoming catalysts, analyze cash runway and potential dilution risk, and generate concise research cards summarizing why a situation is interesting **and** how it could go wrong.

## Current Status

The SEC filings layer now uses the SEC-maintained EDGAR company-submissions JSON endpoint. It can resolve a ticker to CIK, fetch recent filing metadata, identify the latest 10-Q, 10-K, and 8-K, and flag recent financing-related forms such as S-1, S-3, 424B filings, and FWP filings.

The ClinicalTrials.gov layer is still a stub. Cash runway and burn-rate extraction are also still pending because they require XBRL company-facts parsing or full filing text extraction.

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

The output describes why the ticker surfaced, the catalyst, SEC filer match, latest 10-Q/10-K/8-K metadata, dilution-risk flag, evidence quality, main risk, and next diligence steps.

## Next Engineering Step

Replace the ClinicalTrials.gov stub with a real trial search client that maps public company tickers to sponsor/legal names and returns active trials, phases, enrollment, endpoints, and estimated completion dates.
