# Design — Zip / State / Federal District Validation (NationBuilder V2)

## Overview

The system pulls every signup from the **NationBuilder V2 API** (OAuth 2.0),
filters to the population worth validating, runs two data-integrity checks per
record, writes a full annotated dataset plus a problems report per test (each CSV
and XLSX), and — when configured — uploads the files to S3 (date-stamped) and
emails the problem reports as attachments.

It runs in two environments from one codebase:

- **Locally** — `python validate_zip_fed.py`, reading `.env`, writing to the
  current directory. S3/email are skipped unless configured.
- **In AWS Lambda** — `validate_zip_fed.handler`, on a nightly EventBridge
  schedule, loading OAuth credentials from Secrets Manager, writing to `/tmp`,
  uploading to S3, and emailing.

Design principle: **one code path, behavior gated by environment variables.**
Every cloud feature (S3, Secrets Manager, S3-sourced reference, email) is a no-op
when its env var is unset, so the same module is a simple local script or a full
cloud job depending only on configuration.

NationBuilder V2 is the sole data source. The former Insights/Tableau and V1
code has been removed.

## Architecture

```
                 EventBridge Scheduler (nightly, cron 0 6 * * ? * UTC)
                                   │ invokes
                                   ▼
                    ┌───────────────────────────────┐
   Secrets Manager  │  Lambda: zipstatefed-          │   AWS-managed pandas
   nb/v2/prod  ◀───▶│  validation                   │◀─ layer (pandas/numpy)
   (OAuth creds +   │  handler -> run()             │
    rotated token)  │  (pull, filter, validate,     │
                    │   write, upload, email)        │
   S3 refs/ZIP_ ───▶└───────────────────────────────┘
   Locale_Detail.xlsx       │            │
                 NationBuilder V2 ◀───────┘            │ writes /tmp, uploads S3
                 /api/v2/signups                       ▼
                                        S3 zipstatefed-reports-<acct>/
                                          zip-state-reports/*_YYYYMMDD.csv/.xlsx
                                                       │
                                                       ▼ attachments (problem reports)
                                                     Amazon SES ──▶ recipients
```

### Source modules

```
validate_zip_fed.py          The pipeline (local main() + Lambda handler())
nationbuilder_v2/            NationBuilder V2 client package
  client.py                   OAuth refresh-token client; parallel paged pull
  extract.py                  project + filter signups to the needed fields
  exceptions.py
build_lambda_zip.py          Builds the Lambda deployment zip
Dockerfile / .dockerignore   Optional container-image packaging (fallback)
aws/*.json                   IAM policies + Lambda env config
DEPLOY.md                    Deployment guide
V2_OAUTH_SETUP.md            OAuth flow + V2 schema reference
ZIP_REFERENCE_AND_ERRORS.md  ZIP reference layout + error messages
```

## Components

### 1. `nationbuilder_v2/client.py` — V2 API client

- `NationBuilderV2Client.from_env(on_refresh=...)` builds a client from the
  `NATIONBUILDER_*` env vars and immediately mints an access token from the
  refresh token.
- `refresh_access_token()` POSTs the refresh token to `/oauth/token`; on success
  it stores the new access token and, if the refresh token rotated, calls
  `on_refresh(new_token)` so the caller can persist it.
- `fetch_all_signups(extra_fields, concurrency, ...)` pulls every signup in
  parallel. V2 uses **page-number** pagination, so pages are requested directly
  by number via a thread pool; it fetches in batches and stops when a whole batch
  returns empty (the `stats[total]` count proved unreliable, so end-of-data is
  detected by empty pages). `_get_page` retries on 429/5xx with backoff.

### 2. `nationbuilder_v2/extract.py` — project + filter

- `project_signup(record)` → `{signup_id, registered_state, registered_zip,
  federal_district, us_citizen, date_last_verified}`. State/ZIP come from
  `registered_address`; `federal_district` is top-level; `us_citizen` and
  `date_last_verified` come from `custom_values`. `_norm_zip` takes the first 5
  digits.
- `keep_row` / `exclusion_reason` implement the inclusion filter (non-blank zip,
  truthy `us_citizen`, non-blank `date_last_verified`).
- `extract_filtered_signups(concurrency, on_refresh)` pulls all signups, projects,
  filters, and returns `(kept_rows, stats)`.

### 3. `validate_zip_fed.py` — the pipeline

`run()` (returns a result dict; never raises — errors are captured):

1. `_load_secret_into_env()` — if `NATIONBUILDER_SECRET_ID` is set, load the
   OAuth creds from Secrets Manager (setdefault, so env/`.env` wins).
2. `resolve_reference_file()` — local workbook, or download from
   `ZIP_LOOKUP_S3_URI`.
3. `build_zip_to_state()` — merge the three ZIP-reference sheets.
4. `extract_filtered_signups(..., on_refresh=_persist_refresh_token)` — pull +
   filter from V2.
5. Build a DataFrame; run the two checks (`_zip_state_result`,
   `_federal_district_result`); zero-pad the ZIP for display.
6. Write the full dataset + two per-test problem reports (CSV + XLSX).
7. `upload_to_s3()` (date-stamped) if `S3_BUCKET` set; `notify_by_email()` if
   `SES_SENDER` set.
8. Write the status file; return the result dict.

`main()` calls `run()`; `handler(event, context)` returns
`{statusCode, result}`.

### Refresh-token persistence

`_persist_refresh_token(new_token)` writes the rotated refresh token back into
the `nb/v2/prod` secret in Lambda (where `/tmp` and env are not durable), or to
`.env` locally. This is wired as the client's `on_refresh` callback so a
scheduled run always starts with a usable token.

## Decision log — why it is built this way

### Why NationBuilder V2 as the sole source

The federal_district value from Insights was unreliable; NationBuilder holds the
authoritative per-member value, and V2 exposes `registered_address` (state/ZIP)
and `federal_district` directly on the signup. Sourcing everything from one
authoritative API removed the need for the old Insights pull and the V1
cross-check entirely.

### Why parallel page fetching

V2 caps `page[size]` at 100 → ~1,886 pages for ~188k signups. Serial paging is
~19 min (over the Lambda max). V2's page-number pagination lets us request pages
directly, so a thread pool (default 20) brings a full pull to ~4–5 min with no
rate-limit issues observed. End-of-data is detected by empty pages because the
`stats[total]` count was intermittent.

### Why 3008 MB memory

The full pull holds ~188k records (nested address + custom-value dicts) plus a
pandas DataFrame — ~2–3 GB peak. At 1024 MB the function was killed with
`Runtime.OutOfMemory`; 3008 MB runs comfortably and (as more memory = more CPU)
faster.

### Why the inclusion filter

Only US-registered, citizen, verified members are meaningful ZIP/state/district
targets. `us_citizen` and `date_last_verified` live in `custom_values` (DA custom
fields), so the pull requests `custom_values` and the filter reads them there.

### Why both CSV and XLSX

Excel strips leading zeros from a CSV on open; the XLSX types the ZIP column as
text so Excel keeps them. The CSV is the universal form for everything else.

### Why Secrets Manager with write-back

The OAuth client secret and refresh token are sensitive and belong in Secrets
Manager, not plain function env vars. V2 rotates the refresh token on each
refresh, so the function writes the new token back to the secret — otherwise a
scheduled run would eventually fail with a stale token. This is why the IAM policy
grants `PutSecretValue` as well as `GetSecretValue`.

### Why email attachments (not links)

The audience is small and known; attachments open straight from the inbox with no
link/expiry to manage. Only the problem reports are attached (the full dataset is
large and stays in S3); a size guard skips any attachment over the SES limit.

### Why date-stamped S3 objects

To build a history: each nightly run leaves distinct `_YYYYMMDD` objects rather
than overwriting, so past reports can be compared. Local file names stay undated.

## Result / error handling

`run()` wraps everything in try/except, writes `STATUS: OK`/`STATUS: ERROR` (with
traceback) to `validate_zip_fed.status.txt` and stdout, and returns a result
dict. The Lambda returns `statusCode 500` with a diagnosable body on failure; the
full traceback is in CloudWatch Logs.

## Deployment (summary; full steps in DEPLOY.md)

Live in AWS account `068238656047`, region `eu-west-2`:

- **S3** `zipstatefed-reports-068238656047` (Block Public Access on); reference at
  `refs/`, reports at `zip-state-reports/`, recipients at `config/recipients.txt`.
- **Secrets Manager** `nb/v2/prod` (JSON of `NATIONBUILDER_*` OAuth creds; refresh
  token rotated in place each run).
- **IAM role** `zipstatefed-lambda-role` — scoped logs, Get+Put on `nb/v2/prod`,
  S3 Get/Put on the bucket, SES send scoped to the from-address.
- **Layer** `arn:aws:lambda:eu-west-2:336392948345:layer:AWSSDKPandas-Python312:31`.
- **Function** `zipstatefed-validation` — python3.12, handler
  `validate_zip_fed.handler`, 3008 MB, 900 s.
- **Schedule** EventBridge Scheduler `zipstatefed-nightly`, `cron(0 6 * * ? *)`
  UTC.

## Configuration reference (environment variables)

| Variable | Purpose | Default |
|----------|---------|---------|
| `NATIONBUILDER_SLUG` / `_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` | V2 OAuth creds | from `.env` / secret |
| `NATIONBUILDER_SECRET_ID` / `_SECRET_REGION` | Secrets Manager secret to load creds from | unset (local) |
| `NB_LOOKUP_CONCURRENCY` | Parallel page-fetch workers | 20 |
| `OUTPUT_DIR` | Where files are written | `.` (`/tmp` in Lambda) |
| `ZIP_MODE` | `delivery` or `physical` ZIP key | `delivery` |
| `ZIP_LOOKUP_S3_URI` | Fetch reference workbook from S3 | unset (local file) |
| `S3_BUCKET` / `S3_PREFIX` / `S3_REGION` | S3 upload target | unset (no upload) |
| `PRESIGN_EXPIRY_SECONDS` | Presigned URL lifetime | 604800 (7 days) |
| `SES_SENDER` / `SES_REGION` | Email sender + region | unset (no email) |
| `RECIPIENTS_S3_URI` / `RECIPIENTS_FILE` | Recipient list source | `recipients.txt` |
| `EMAIL_MESSAGE` | Free-text body message | CSV-vs-XLSX explanation |

## Security

- `.env` (OAuth creds/tokens) and all report CSV/XLSX (member PII) are gitignored.
- `.env.example` holds placeholders only.
- The status file (member data) and the Lambda invoke response (presigned URLs)
  are gitignored.
- S3 bucket has Block Public Access on; IAM is least-privilege; the client secret
  and refresh token live only in `.env` / Secrets Manager.
