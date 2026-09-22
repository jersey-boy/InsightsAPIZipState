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
| `pull_zip_state_federal.py` | Pulls the "Zip State Federal District" view, runs two independent checks (ZIP vs. state, federal district vs. state), and writes a full CSV plus one problems report per check. |
| `sources.py` | Original sample: lists data source fields. |

Run any script with:

```bash
.venv/bin/python <script>.py
```

## Validation output

`pull_zip_state_federal.py` runs two independent checks and produces six files
(all gitignored, as they contain member PII):

**Full dataset** (every record, both problem-flag columns):

- `zip_state_federal_district.csv` / `.xlsx` — the `.xlsx` types the ZIP column
  as text so Excel keeps leading zeros.

**Test 1 — ZIP / state** (only rows that failed this check):

- `zip_state_problems.csv` / `.xlsx` — carries the `zip_state_problem` flag.

**Test 2 — federal district / state** (only rows that failed this check):

- `federal_district_problems.csv` / `.xlsx` — carries the
  `federal_district_problem` flag.

The two problem reports are independent; a member flagged by both checks appears
in both files. A passing check leaves the flag blank. Problem messages are
explicit, e.g. `ZIP STATE PROBLEM. SHOULD BE VA`,
`ZIP STATE PROBLEM. ZIP IS BLANK`, `ZIP STATE PROBLEM. ZIP NOT FOUND`,
`FEDERAL DISTRICT PROBLEM`, `FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK`.

## ZIP reference file

> For a deep dive on the reference file layout and what every error message
> means, see **[`ZIP_REFERENCE_AND_ERRORS.md`](ZIP_REFERENCE_AND_ERRORS.md)**.

ZIP → state validation uses the USPS **`ZIP_Locale_Detail.xlsx`** workbook, which
must sit alongside the scripts (it is gitignored). The workbook has three sheets
that are merged into a single ZIP → state lookup (~41,500 ZIPs):

| Sheet | Header row | Notes |
|-------|-----------|-------|
| `Detail` | 1 | Main locale list; authoritative, wins on conflicts. |
| `Unique` | 3 | Unique / single-entity ZIPs (universities, PO-box-only, etc.). |
| `Other`  | 3 | Remaining ZIPs. |

Using `Detail` alone omits ~195 valid ZIPs that live on `Unique`/`Other`, so all
three are combined.

### Physical vs. delivery ZIP

The `ZIP_MODE` constant in `pull_zip_state_federal.py` selects which ZIP keys the
lookup:

- `"delivery"` (default) — keyed by the delivery ZIP. Broadest coverage; only ~50
  member ZIPs end up `ZIP NOT FOUND` (placeholder / unassigned ZIPs).
- `"physical"` — keyed by the physical (facility) ZIP. Narrower coverage, so many
  valid member ZIPs get flagged `ZIP NOT FOUND`; use only if you specifically want
  to validate against the physical facility ZIP.

The workbook only carries a *physical* state, so both modes validate the ZIP
against that physical state — the mode only changes which ZIP column is the key.

## Spec

Full requirements, design, and tasks live in
`.kiro/specs/zip-state-federal-validation/`.

## Notes

- Requires the `ZIP_Locale_Detail.xlsx` reference workbook alongside the scripts
  (see [ZIP reference file](#zip-reference-file)). It is gitignored.
- Set `NB_INSIGHTS_API_VERSION=3.23` in `.env` to match the server (the server
  is on REST API 3.23; a lower value logs a warning and can break sign-out).
- On Windows, run scripts with `.venv\Scripts\python.exe <script>.py` instead of
  the `.venv/bin/python` shown above.
