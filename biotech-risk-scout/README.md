# Biotech Risk Scout

This project is an early prototype of an **AI Special Situations Research Agent** focused on small-cap biotechnology companies. It aims to surface tickers with upcoming catalysts, analyze their cash runway and potential dilution risk, and generate concise research cards summarizing why a situation is interesting **and** how it could blow up.

At this stage, the implementation consists of stubs that illustrate the expected architecture. Future work will replace these stubs with real integrations to the SEC EDGAR API, ClinicalTrials.gov, openFDA, and market data sources.

## Layout

```txt
biotech-risk-scout/
├── app/               # CLI entry point
├── scout/
│   ├── ingest/        # Data ingestion modules
│   └── reports/       # Report and research card abstractions
└── README.md
```

## Ingestion

The `scout.ingest` package currently defines two functions:

- `fetch_sec_filings(ticker)` returns a placeholder dictionary with mock financial information extracted from SEC filings.
- `fetch_clinical_trials(ticker)` returns a placeholder dictionary describing an upcoming clinical trial catalyst.

These functions should be expanded to retrieve and parse real data.

## Reports

`scout.reports.research_card` defines a `ResearchCard` dataclass that holds the key attributes of a diligence memo. It provides a factory method `from_sources()` to construct a card from ingestion outputs and a `to_text()` method to render the card as plain text.

## CLI

Run the CLI with:

```bash
python biotech-risk-scout/app/main.py TICKER
```

Example:

```bash
python biotech-risk-scout/app/main.py ABCD
```

The output describes why the ticker surfaced, the catalyst, cash runway, dilution risk, evidence quality, main blow-up risk, and suggested next diligence steps.
