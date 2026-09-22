# Design — Zip / State / Federal District Validation

## Overview

The system is a small set of Python scripts that connect to NationBuilder
Insights (Tableau REST API), pull the "Zip State Federal District" view, run two
data-integrity checks per record, and emit two CSV files. Credentials live in a
`.env` file loaded at runtime.

The design favors simplicity: a single shared connection module plus one script
per app. There is no long-running service, database, or scheduler — each run is
a one-shot extract-and-validate.

## Architecture

```
                       .env  (credentials, not committed)
                         │
                         ▼
   ┌──────────────────────────────────┐
   │ nb_insights.py                    │   Shared connection helper
   │  - build_config() from env vars   │
   │  - connect() -> signed-in conn    │
   │  - insights_connection() ctx mgr  │
   └──────────────────────────────────┘
                         │ imported by
        ┌────────────────┴─────────────────┐
        ▼                                   ▼
 ┌──────────────────┐            ┌───────────────────────────┐
 │ list_reports.py  │            │ pull_zip_state_federal.py │
 │  lists workbooks │            │  pull view + validate     │
 │  views, sources  │            │  + write output CSVs      │
 └──────────────────┘            └───────────────────────────┘
                                        │            │
                 ZIP_Locale_Detail.xlsx             ▼
                 (Detail+Unique+Other)   │   zip_state_federal_district.csv/.xlsx
                                         ├─> zip_state_problems.csv/.xlsx
                                         └─> federal_district_problems.csv/.xlsx
```

## Components

### 1. `nb_insights.py` — connection helper

- Loads `.env` via `python-dotenv`.
- `build_config()` assembles the `tableau_api_lib` config from environment
  variables (`NB_INSIGHTS_SERVER`, `NB_INSIGHTS_API_VERSION`,
  `NB_INSIGHTS_TOKEN_NAME`, `NB_INSIGHTS_TOKEN_SECRET`, `NB_INSIGHTS_SITE_NAME`,
  `NB_INSIGHTS_SITE_URL`).
- `_require(name)` raises `MissingCredentialsError` when a required variable is
  absent (Requirement 1.2).
- `connect()` builds a `TableauServerConnection` and signs in.
- `insights_connection()` is a context manager that signs in, yields the
  connection, and always signs out (Requirement 1.3).

### 2. `list_reports.py` — content inventory (supporting app)

Lists workbooks, views, and data sources and writes each to CSV. Used to locate
view IDs (this is how the target view ID was found). Not part of the core
validation flow but shares the connection module.

### 3. `pull_zip_state_federal.py` — the validation app

Pipeline:

1. **Fetch** — `get_view_data_dataframe(conn, view_id=VIEW_ID)` returns a
   DataFrame. `VIEW_ID = c7f541b2-87db-4fad-97c5-2e532dbbe956`.
2. **Reorder** — arrange columns into the required output order; any unexpected
   columns are appended so no data is lost (Requirement 2.3).
3. **Init flags** — add `zip_state_problem` and `federal_district_problem`,
   initialized blank.
4. **ZIP / state check** — see below.
5. **Federal district check** — see below.
6. **Drop `fed_dist_prefix`** — used only for the test, removed before output
   (Requirement 5.5).
7. **Write** — full annotated dataset (CSV + XLSX), then two separate
   problems reports: one for the ZIP/state test and one for the federal
   district test (each CSV + XLSX).
8. **Report** — write a run summary to `pull_zip_state_federal.status.txt` and
   stdout.

## Data flow and key decisions

### ZIP / state check

- Build a `{zip:int -> state:str}` dictionary from `ZIP_Locale_Detail.xlsx`,
  merging the `Detail`, `Unique`, and `Other` sheets (`Detail` wins on
  conflicts). The ZIP key is the delivery ZIP or physical ZIP per `ZIP_MODE`
  (default `delivery`). See `ZIP_REFERENCE_AND_ERRORS.md` for the full layout.
- ZIP values on both sides are coerced to integers. This deliberately normalizes
  leading-zero ZIPs: the view stores `registered_zip_clean` numerically (e.g.
  `1002`), and the lookup is numeric too, so `1002` on both sides match — the
  equivalent of comparing `01002` to `01002` (Requirement 4.2).
- Result precedence per record:
  1. ZIP blank/non-numeric → `ZIP STATE PROBLEM. ZIP IS BLANK`
  2. ZIP not in lookup → `ZIP STATE PROBLEM. ZIP NOT FOUND`
  3. lookup state == registered state → blank
  4. otherwise → `ZIP STATE PROBLEM. SHOULD BE <lookup state>`

### Federal district check

- Compare `fed_dist_prefix` (the state portion of the federal district, e.g.
  `AK` from `AK0`) to `registered_state`, case-insensitively and trimmed.
- Result precedence per record:
  1. prefix blank/NaN → `FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK`
  2. prefix == registered state → blank
  3. otherwise → `FEDERAL DISTRICT PROBLEM`

### Why blanks mean "passed"

Per the current requirement, a passing check leaves the field empty rather than
writing "OK". This keeps each problems report easy to define (rows where that
test's flag is non-blank) and makes the full file easy to scan for populated
cells.

## Output

Each file is written as both a universal CSV and an Excel-friendly XLSX (the ZIP
column is typed as text so Excel keeps leading zeros).

- **`zip_state_federal_district.csv` / `.xlsx`** — all records; columns:
  `signup_id`, `full_name`, `registered_state`, `registered_zip_clean`,
  `federal_district`, `zip_state_problem`, `federal_district_problem`.
- **`zip_state_problems.csv` / `.xlsx`** (Test 1) — only rows where
  `zip_state_problem` is non-blank; carries that flag and drops
  `federal_district_problem`.
- **`federal_district_problems.csv` / `.xlsx`** (Test 2) — only rows where
  `federal_district_problem` is non-blank; carries that flag and drops
  `zip_state_problem`.

The two problem reports are independent, so a record failing both checks appears
in both files.

## Error handling

- The main routine wraps the pipeline in a try/except and writes either
  `STATUS: OK` with a summary, or `STATUS: ERROR` with the exception and
  traceback, to `pull_zip_state_federal.status.txt` (Requirement 7.2). This is
  important because the workspace lives on an external drive whose shell
  intermittently mangles/aborts command output; the status file is the reliable
  record of a run.
- Network/SSL errors during sign-in are transient and surfaced via the status
  file; the fix is simply to re-run.

## Configuration and environment notes

- Python 3.13 in a `.venv`. On this external drive, venv launcher scripts may not
  be created, so invoke as `.venv/bin/python -m pip ...` and
  `.venv/bin/python <script>.py`.
- Dependencies: `tableau-api-lib`, `pandas`, `python-dotenv` (`requirements.txt`).
- API version is set to `3.23` to match the server.

## Security

- `.env` holds the personal access token and is excluded via `.gitignore`.
- `.env.example` documents the required variables without real values.
- Output CSVs contain member PII (names, ZIPs) and are gitignored by default.
