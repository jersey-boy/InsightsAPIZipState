# InsightsAPIZipState

Python tooling that validates Democrats Abroad member ("signup") data integrity
by pulling every signup from the **NationBuilder V2 API** and checking two things
per record:

1. **ZIP vs. state** — does the member's registered ZIP code belong to their
   registered state?
2. **Federal district vs. state** — does the member's federal congressional
   district belong to their registered state?

NationBuilder V2 (OAuth 2.0) is the sole data source. (An earlier version read
from NationBuilder Insights/Tableau and the V1 API; that code has been removed.)

## Setup

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements.txt   # Windows
   # .venv/bin/python -m pip install -r requirements.txt     # macOS/Linux
   ```

2. Copy `.env.example` to `.env` and fill in your NationBuilder V2 OAuth
   credentials (see [`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md) for how to obtain
   them). `.env` is gitignored and must never be committed.

## Scripts

| Script | Purpose |
|--------|---------|
| `validate_zip_fed.py` | The pipeline. Pulls all signups from NationBuilder V2, filters, runs the two checks, writes a full dataset plus a problems report per check, and (optionally) uploads to S3 and emails the reports. Local entry point `main()`; AWS Lambda entry point `handler()`. |
| `nationbuilder_v2/` | NationBuilder V2 API client: OAuth 2.0 refresh-token flow, parallel full-nation pull, and the signup extractor/filter. |
| `build_lambda_zip.py` | Builds the Lambda deployment zip (deps as Linux wheels; pandas/numpy come from the AWS-managed pandas layer). |

Run the pipeline locally with:

```bash
.venv/Scripts/python validate_zip_fed.py
```

## Data source: NationBuilder V2

The pipeline authenticates to NationBuilder V2 with **OAuth 2.0** and pulls every
signup via `/api/v2/signups?extra_fields[signups]=registered_address,custom_values`.
For each signup it projects:

| Field | V2 source |
|-------|-----------|
| `signup_id` | `data[].id` |
| `registered_state` | `attributes.registered_address.state` |
| `registered_zip` | `attributes.registered_address.zip` (normalized to 5 digits) |
| `federal_district` | `attributes.federal_district` (e.g. `NY13`; prefix `NY` is the state) |
| `us_citizen` | `attributes.custom_values.us_citizen` (custom field) |
| `date_last_verified` | `attributes.custom_values.date_last_verified` (custom field) |

`registered_address` is the member's US voter-registration address (not
`primary_address`, which for Democrats Abroad members is usually overseas).

**Pagination / performance:** V2 caps `page[size]` at 100 (~1,886 pages for the
full ~188k nation). Since V2 uses page-*number* pagination, pages are fetched in
parallel (default 20 workers), so a full pull completes in ~4–5 minutes. See
[`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md) for the OAuth flow and schema details.

### Inclusion filter

Before validation, signups are filtered to the population that should be checked.
A row is **kept** only when all three hold:

- registered ZIP is non-blank,
- `us_citizen` is truthy, and
- `date_last_verified` is non-blank.

The run reports how many rows were excluded by each reason.

## Validation output

`validate_zip_fed.py` runs the two checks and produces six files (all gitignored,
as they contain member PII):

**Full dataset** (every kept record, both problem-flag columns):

- `zip_state_federal_district.csv` / `.xlsx` — the `.xlsx` types the ZIP column
  as text so Excel keeps leading zeros.

**Test 1 — ZIP / state** (only rows that failed this check):

- `zip_state_problems.csv` / `.xlsx` — carries the `zip_state_problem` flag.

**Test 2 — federal district / state** (only rows that failed this check):

- `federal_district_problems.csv` / `.xlsx` — carries the
  `federal_district_problem` flag.

The two problem reports are independent; a member flagged by both appears in both
files. A passing check leaves the flag blank. Problem messages are explicit, e.g.
`ZIP STATE PROBLEM. SHOULD BE VA`, `ZIP STATE PROBLEM. ZIP IS BLANK`,
`ZIP STATE PROBLEM. ZIP NOT FOUND`, `FEDERAL DISTRICT PROBLEM`,
`FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK`.

### Output location and S3 upload

By default files are written to the current directory. Two optional env vars (see
`.env.example`) change this:

- `OUTPUT_DIR` — directory to write into. Defaults to `.`; set to `/tmp` in AWS
  Lambda (the only writable path).
- `S3_BUCKET` (+ optional `S3_PREFIX`, `S3_REGION`) — when set, every output file
  is uploaded to `s3://<bucket>/<prefix>/<name>`. Object names are
  **date-stamped** with the run date, e.g. `zip_state_problems_YYYYMMDD.csv`, so a
  history accumulates in the bucket (local file names stay undated). A presigned
  URL is also generated per object. When unset, output stays local only.

No AWS keys are configured in this project: locally `boto3` uses your AWS
profile/environment, and in Lambda it uses the function's IAM execution role.

### Email notification

When `SES_SENDER` is set and a recipient list exists (`RECIPIENTS_S3_URI` or a
local `RECIPIENTS_FILE`, one address per line, `#` comments), the run emails the
two **problem reports as file attachments** via Amazon SES — the full dataset is
never attached (it is uploaded to S3 for reference). Attachments are named with
the run date. The body carries the summary counts and the `EMAIL_MESSAGE` note.
An attachment is skipped (with a note in the body) if it would exceed the SES
~10 MB limit. Email is a no-op unless `SES_SENDER` is set.

## ZIP reference file

> For a deep dive on the reference file layout and what every error message
> means, see **[`ZIP_REFERENCE_AND_ERRORS.md`](ZIP_REFERENCE_AND_ERRORS.md)**.

ZIP → state validation uses the USPS **`ZIP_Locale_Detail.xlsx`** workbook, which
must sit alongside the scripts locally (gitignored) or be sourced from S3 via
`ZIP_LOOKUP_S3_URI`. The workbook has three sheets merged into a single
ZIP → state lookup (~41,500 ZIPs):

| Sheet | Header row | Notes |
|-------|-----------|-------|
| `Detail` | 1 | Main locale list; authoritative, wins on conflicts. |
| `Unique` | 3 | Unique / single-entity ZIPs (universities, PO-box-only, etc.). |
| `Other`  | 3 | Remaining ZIPs. |

Using `Detail` alone omits ~195 valid ZIPs that live on `Unique`/`Other`, so all
three are combined. `ZIP_MODE` (`delivery` default, or `physical`) selects which
ZIP column keys the lookup.

## Running as an AWS Lambda

`validate_zip_fed.handler` is the Lambda entry point. It writes outputs to
`/tmp`, uploads them to S3, emails the reports, and loads its NationBuilder OAuth
credentials from AWS Secrets Manager (rotating the refresh token back into the
secret each run). Build the deployment zip with `python build_lambda_zip.py`
(bundles the non-pandas deps as Linux wheels; pandas/numpy come from the
AWS-managed pandas layer). See **[`DEPLOY.md`](DEPLOY.md)** for the full
step-by-step.

## Spec

Full requirements, design, and tasks live in
`.kiro/specs/zip-state-federal-validation/`.

## Notes

- Requires the `ZIP_Locale_Detail.xlsx` reference workbook (local or via
  `ZIP_LOOKUP_S3_URI`). It is gitignored.
- The full pull holds ~188k records in memory, so the Lambda is provisioned at
  3008 MB (and 900s timeout).
- On Windows, run scripts with `.venv\Scripts\python.exe <script>.py`.
