# InsightsAPIZipState

Python tools that access the **NationBuilder Insights** reporting system (built
on Tableau) via the Tableau REST API, and validate member ZIP / state / federal
district data integrity.

## Setup

1. Create and activate a virtual environment, then install dependencies:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your NationBuilder Insights
   personal access token and site details:

   ```bash
   cp .env.example .env
   ```

   `.env` is gitignored and must never be committed.

## Scripts

| Script | Purpose |
|--------|---------|
| `nb_insights.py` | Shared connection helper. Loads `.env`, provides `insights_connection()` context manager. Run directly for a sign-in smoke test. |
| `list_reports.py` | Lists all Insights workbooks, views, and data sources; writes each to CSV. |
| `pull_zip_state_federal.py` | Pulls the "Zip State Federal District" view, validates ZIP vs. state and federal district vs. state, and writes a full CSV plus a problems-only CSV. |
| `sources.py` | Original sample: lists data source fields. |

Run any script with:

```bash
.venv/bin/python <script>.py
```

## Validation output

`pull_zip_state_federal.py` produces two files (both gitignored, as they contain
member PII):

- `zip_state_federal_district.csv` — all records with two problem-flag columns.
- `zip_state_federal_district_problems.csv` — only records that failed a check.

A passing check leaves the flag blank. Problem messages are explicit, e.g.
`ZIP STATE PROBLEM. SHOULD BE VA`, `ZIP STATE PROBLEM. ZIP IS BLANK`,
`FEDERAL DISTRICT PROBLEM`, `FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK`.

## Spec

Full requirements, design, and tasks live in
`.kiro/specs/zip-state-federal-validation/`.

## Notes

- Requires a `zip_state_lookup.csv` reference file (ZIP → state) alongside the
  scripts. It is gitignored.
- API version is set to `3.23` to match the server.
