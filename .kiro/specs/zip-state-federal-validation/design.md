# Design — Zip / State / Federal District Validation

## Overview

The system pulls the "Zip State Federal District" NationBuilder Insights view
(via the Tableau REST API), removes member names, runs two data-integrity checks
per record, writes a full annotated dataset plus a problems report per test (each
as CSV and XLSX), and — when configured — uploads the files to S3 and emails
presigned download links.

It runs in two environments from a single codebase:

- **Locally** — `python pull_zip_state_federal.py`, reading `.env`, writing files
  to the current directory. S3/email are skipped unless configured.
- **In AWS Lambda** — `pull_zip_state_federal.handler`, on a nightly EventBridge
  schedule, reading credentials from Secrets Manager, writing to `/tmp`, and
  uploading to S3 + emailing links.

The design principle throughout: **one code path, behavior gated by
environment variables.** Every cloud feature (S3 upload, Secrets Manager,
S3-sourced reference, email) is a no-op when its env var is unset, so the exact
same module is a simple local script or a full cloud job depending only on
configuration. This keeps local development friction-free and avoids a separate
"Lambda version" that could drift.

## Architecture

```
                         EventBridge Scheduler (nightly, cron 0 6 * * ? * UTC)
                                       │ invokes
                                       ▼
                          ┌─────────────────────────────┐
        Secrets Manager   │  Lambda: zipstatefed-        │   AWS-managed pandas
        nb/insights/prod ─┼─▶ validation                │◀─ layer (pandas/numpy)
                          │  handler -> run()            │
   S3 refs/ZIP_Locale_ ──┼─▶ (pull, validate, write,    │
   Detail.xlsx           │   upload, email)             │
                          └─────────────────────────────┘
                                   │            │
                    NationBuilder  │            │  writes /tmp, uploads to S3
                    Insights  ◀────┘            ▼
                    (Tableau API)      S3 zipstatefed-reports-<acct>/
                                         zip-state-reports/*.csv/.xlsx
                                       │
                                       ▼  presigned links (problem reports only)
                                     Amazon SES ──▶ recipients (config/recipients.txt)
```

Local runs collapse this to: `.env` → `pull_zip_state_federal.py` → files in the
current directory (no S3/SES).

### Source modules

```
nb_insights.py               Shared Tableau connection + credential loading
pull_zip_state_federal.py    The validation app (local main() + Lambda handler())
list_reports.py              Supporting: lists Insights workbooks/views/sources
build_lambda_zip.py          Builds the Lambda deployment zip (Linux wheels)
Dockerfile / .dockerignore   Optional container-image packaging (fallback)
aws/*.json                   IAM policies + Lambda env config used to deploy
DEPLOY.md                    Step-by-step AWS deployment guide
ZIP_REFERENCE_AND_ERRORS.md  Deep dive on the reference file + error messages
```

## Components — how each piece works

### 1. `nb_insights.py` — connection + credentials

- `load_dotenv()` loads `.env` into the environment (no-op if the file is
  absent, e.g. in Lambda).
- `_load_secret_into_env()` runs at import, *after* `load_dotenv()`. If
  `NB_INSIGHTS_SECRET_ID` is set, it reads a JSON secret from Secrets Manager and
  copies each key into `os.environ` with `setdefault` — so anything already set
  (local `.env` / shell) wins, and the secret only fills gaps. It handles
  `SecretString` or base64 `SecretBinary`, strips a UTF-8 BOM, and raises a clear
  `RuntimeError` on empty/invalid JSON. No-op when the secret id is unset.
- `build_config()` assembles the `tableau_api_lib` config from the
  `NB_INSIGHTS_*` variables. `_require()` raises `MissingCredentialsError` for
  any missing one (Req 1.2).
- `insights_connection()` is a context manager that signs in, yields the
  connection, and always signs out (Req 1.3).

### 2. `pull_zip_state_federal.py` — the validation app

**Two entry points over one core:**

- `run()` — the whole pipeline; returns a result dict
  (`status`, `rows`, `zip_state_problems`, `federal_district_problems`, `files`,
  `presigned_urls`, `emailed_to`, and `error` on failure). It never raises: any
  exception is caught, recorded in the result and the status file, and returned,
  so a Lambda always gets a clean response.
- `main()` — local entry point, just calls `run()`.
- `handler(event, context)` — Lambda entry point; calls `run()` and returns
  `{"statusCode": 200|500, "result": ...}`.

**Pipeline inside `run()`:**

1. **Resolve reference** — `resolve_reference_file()`: if `ZIP_LOOKUP_S3_URI` is
   set, download the workbook to `OUTPUT_DIR`; else use the local
   `ZIP_Locale_Detail.xlsx`.
2. **Fetch** — `get_view_data_dataframe(conn, view_id=VIEW_ID)`.
3. **Drop PII** — remove `full_name` (Req 2.2).
4. **Reorder** — required column order; unexpected columns appended (Req 2.4).
5. **Init flags** — add blank `zip_state_problem` / `federal_district_problem`.
6. **ZIP/state check** — build the merged lookup, then apply per-row (below).
7. **Federal district check** — prefix vs. state, per-row (below).
8. **Drop `fed_dist_prefix`** (Req 5.5).
9. **Zero-pad ZIP** to 5-digit text for display (after the numeric checks).
10. **Write** — full dataset + two per-test reports, each CSV + XLSX, via
    `_out()` (which joins `OUTPUT_DIR` and creates it).
11. **Upload** — `upload_to_s3()` if `S3_BUCKET` set; collect presigned URLs.
12. **Email** — `notify_by_email()` if `S3_BUCKET` and `SES_SENDER` set.
13. **Status** — write `pull_zip_state_federal.status.txt` and print.

### 3. `build_zip_to_state(reference_file, mode)`

Builds the `{zip:int -> state:str}` map. Iterates `Detail`, then `Unique`, then
`Other`, reading each with its own header row and column names, and inserts with
`setdefault` so `Detail` wins on conflicts. ZIPs are coerced to int. See the
decision log for why all three sheets and why integer keys.

### 4. `upload_to_s3(paths)` / `notify_by_email(urls, summary)`

- `upload_to_s3` — lazy-imports boto3, uploads each file to
  `s3://S3_BUCKET/S3_PREFIX/<dated-name>` (the object name is date-stamped via
  `_dated_name()` so history accumulates), and returns a presigned GET URL per
  object (`PRESIGN_EXPIRY_SECONDS`). No-op returning `[]` when `S3_BUCKET` unset.
- `load_recipients()` — reads the recipient list from `RECIPIENTS_S3_URI` (S3) or
  `RECIPIENTS_FILE` (local), one address per line, skipping blanks and `#`.
- `notify_by_email` — no-op unless `SES_SENDER` and recipients exist. Attaches
  the **problem-report files** (excluding `EMAIL_EXCLUDE_FILES`, the full
  dataset) to a raw MIME multipart message and sends via `ses.send_raw_email`.
  Attachments use dated names; any that would exceed `SES_MAX_ATTACH_BYTES`
  (~7 MB raw, keeping under the SES 10 MB post-encoding limit) are skipped and
  noted in the body. The body carries counts + `EMAIL_MESSAGE` only, never inline
  data. Independent of S3. `_build_link_email()` preserves the older
  presigned-link body as a documented, unused alternative.
- `_dated_name(filename)` — inserts `RUN_DATE` (UTC `YYYYMMDD`, computed once per
  process) before the extension: `foo.csv` -> `foo_YYYYMMDD.csv`.
- `nb_person_fields(signup_ids)` — returns
  `{signup_id -> {state, zip, federal_district}}` from the NationBuilder V1 API
  for the flagged ids only, using a thread pool (`NB_LOOKUP_CONCURRENCY`). State
  and zip come from the member's `registered_address`; district from the
  top-level field. Markers for the awkward cases: `""` (NB has none),
  `NOT FOUND`, `LOOKUP ERROR`. No-op `{}` when NB creds are unset.
  `nb_federal_districts()` is a thin wrapper returning just the district (used
  for the federal-district report's cross-check column).
- `_build_insights_vs_nb(df, flagged_ids, nb_fields)` — builds the third report:
  compares state/zip/federal_district between Insights and NB for the union of
  flagged ids, with per-field MATCH/MISMATCH/blank flags, keeping only rows with
  at least one real difference. `_norm_zip()` normalizes ZIPs (first 5 digits,
  zero-padded) for a fair comparison.
- In `run()`, a single `nb_person_fields` lookup over the union of both reports'
  flagged ids feeds both the federal-district cross-check column and the third
  report, so NB is queried once per flagged id regardless of which test flagged
  it.

### 3b. `nationbuilder/` — vendored NB V1 API client

A small, self-contained package copied from the `NBGet01` project
(`client.py`, `models.py`, `exceptions.py`, `auth.py`). `NationBuilderClient`
calls `https://{slug}.nationbuilder.com/api/v1` with an `access_token` query
param over `httpx`, with 429 retry and typed exceptions. We use `get_person(id)`
and read `federal_district` from the person's raw payload. `httpx` is bundled
into the Lambda zip (not in the base runtime).

## Decision log — why it is built this way

### Why delivery ZIP by default (not physical)

Physical-ZIP mode produced ~33,570 "ZIP NOT FOUND" rows — a coverage artifact,
because the physical (facility) ZIP column covers ~29K ZIPs, while members enter
delivery ZIPs. Delivery ZIP covers ~37K and matches member data, cutting
not-found to a few hundred. `ZIP_MODE` keeps physical available for the rare case
someone wants facility-ZIP validation.

### Why merge all three workbook sheets

Using `Detail` alone left ~195 valid US ZIPs flagged as "ZIP NOT FOUND" —
university, PO-box-only, and single-entity ZIPs that live on the `Unique`/`Other`
sheets. Merging all three (Detail authoritative) resolved them. This also settled
the question of whether to add a "non-US/APO" message: investigation showed the
remaining not-found ZIPs are placeholder/unassigned values, not non-US, so no
such message was added.

### Why integer ZIP keys

Both the view and the reference store ZIPs numerically. Comparing as integers
makes `1002` == `1002` (i.e. `01002` == `01002`) with no string padding, and
padding is applied only at the very end for display. Doing the checks on the
numeric value avoids a whole class of leading-zero bugs.

### Why split into two problem reports

The two checks are reviewed by different concerns; a combined file forced
reviewers to filter. Per-test reports (each carrying only its own flag) let a
steward open exactly the failures they care about. The full annotated file is
retained for anyone wanting both flags together.

### Why both CSV and XLSX

Excel silently strips leading zeros from a CSV on open; the XLSX types the ZIP
column as text so Excel keeps them. The CSV is the universal form for everything
else. Rather than pick one and lose a use case, we emit both — and the email
explains this to recipients.

### Why S3 + presigned URLs (not Google Drive)

The destination was a personal "Shared with Me" Google folder, and the app was
moving to AWS. Google Drive from Lambda would mean storing Google credentials and
fighting the service-account storage-quota limitation on shared folders. S3 is
the native Lambda sink, needs only the execution role, and presigned URLs give
credential-free, self-expiring downloads while the bucket stays private (Block
Public Access on).

### Why zip + managed pandas layer (not Docker) in production

pandas has compiled wheels that don't run if built on Windows; a container image
avoids that but needs the Docker engine and an ECR repo. The AWS-managed pandas
layer (`AWSSDKPandas-Python312`) supplies pandas/numpy, so `build_lambda_zip.py`
only needs to bundle the lighter deps as Linux wheels (`--platform
manylinux2014_x86_64`) and prune pandas/numpy — yielding a ~3 MB zip deployable
with the plain CLI. The `Dockerfile` remains as a fallback if deps grow.

### Why Secrets Manager (not plain Lambda env vars)

The Tableau token secret is sensitive; plain function env vars are visible in the
console and to anyone with `GetFunctionConfiguration`. Secrets Manager keeps it
out of the function config, and the `setdefault` loading means local `.env` still
wins so the same code runs both places.

### Why email problem reports only

The full dataset is large and rarely needed for day-to-day review; attaching it
in every email adds weight and spreads more PII, and it would exceed the SES
10 MB limit. It is still uploaded to S3 for reference, but the email attaches
only the two problem reports.

### Why attachments (and why the link alternative is kept)

Attachments let the small, known audience open the reports straight from their
inbox — no link to click, no expiry to track. This needs a raw MIME message
(`ses.send_raw_email`), so `notify_by_email` builds a `MIMEMultipart` and the
IAM role grants `ses:SendRawEmail`. A `SES_MAX_ATTACH_BYTES` guard skips any
attachment that would breach the SES size limit (noting it in the body) so a data
spike degrades gracefully. The earlier presigned-link approach is retained as
`_build_link_email()` (unused) in case the audience grows or files get large
enough that links become preferable again.

### Why cross-check federal district against NationBuilder (and show both)

The Insights `federal_district` is unreliable — testing found it blank or wrong
for many flagged records while NationBuilder had the correct district. Rather
than replace one source with the other, the report shows both (`federal_district`
from Insights, `nb_federal_district` from NB) plus a `MATCH`/`MISMATCH` flag, so a
steward can judge each case. The lookup hits the NB API only for the flagged ids
(~1,000), one call each, parallelized with a thread pool to stay under the Lambda
timeout (which was raised to 900 s as a safety margin). The NB client is vendored
rather than re-implemented, reusing the proven `NBGet01` code. It is entirely
optional: unset NB creds -> cross-check skipped, reports unchanged.

### Why a third "Insights vs NationBuilder" report

The federal-district cross-check only compares one field. Once we were already
fetching each flagged member from NB, comparing all three "registered" fields
(state, zip, federal district) was cheap and surfaced discrepancies the per-test
reports can't — e.g. a ZIP or state that disagrees between the two systems. The
report covers the union of flagged records (every record with a known Insights
problem), shows both sources side by side with per-field flags, and keeps only
rows with a real difference so it is an actionable worklist, not a dump. NB
state/zip come from `registered_address` specifically (not `primary_address`,
which for Democrats Abroad members is typically their overseas address).

### Why date-stamp S3 objects

The goal is a history of runs, not just the latest. Date-stamping each S3 object
name (`_YYYYMMDD`, UTC, computed once per run) means nightly runs leave distinct
objects instead of overwriting, so past reports remain available for comparison.
Local file names stay undated so repeated local runs overwrite rather than pile
up on a developer's disk.

## Result / error handling

`run()` wraps everything in try/except. On success it writes `STATUS: OK` plus
the counts and (when configured) the presigned URLs and the emailed recipients to
`pull_zip_state_federal.status.txt` and stdout, and returns the result dict. On
failure it records `STATUS: ERROR`, the exception repr, and the traceback, and
returns a result with `status="ERROR"` and `error=...` — so the Lambda response
is `statusCode 500` with a diagnosable body, and the full traceback lands in
CloudWatch Logs. The status file exists because, during development, the
workspace drive's shell intermittently mangled command output, making a written
record the reliable source of truth.

## Deployment (summary; full steps in DEPLOY.md)

Deployed to AWS account `068238656047`, region `eu-west-2`:

- **S3** `zipstatefed-reports-068238656047` (Block Public Access on); reference at
  `refs/`, reports at `zip-state-reports/`, recipients at `config/recipients.txt`.
- **Secrets Manager** `nb/insights/prod` (JSON of `NB_INSIGHTS_*` plus the
  `NATIONBUILDER_*` creds for the cross-check).
- **IAM role** `zipstatefed-lambda-role`, least-privilege inline policy: scoped
  CloudWatch Logs, `secretsmanager:GetSecretValue` on the secret,
  `s3:GetObject/PutObject` on the bucket, and `ses:SendEmail`/`ses:SendRawEmail`
  conditioned on the verified from-address (`SendRawEmail` is what carries the
  attachments).
- **Layer** `arn:aws:lambda:eu-west-2:336392948345:layer:AWSSDKPandas-Python312:31`.
- **Function** `zipstatefed-validation` (python3.12, 1024 MB, 900 s — raised from
  300 s to accommodate the NationBuilder cross-check's ~1,000 API calls).
- **Schedule** EventBridge Scheduler `zipstatefed-nightly`, `cron(0 6 * * ? *)`
  UTC, via `zipstatefed-scheduler-role`.
- **SES** domain `lategothikdata.com` verified by DKIM (Route 53); sender
  `insights-reports@lategothikdata.com`. SES is in the sandbox, so the recipient
  is also verified.

### A real bug worth recording: the Secrets Manager BOM

The first Lambda invoke failed with a cryptic `JSONDecodeError` at char 0. Root
cause: the secret's JSON had been written with PowerShell `Set-Content -Encoding
utf8`, which prepends a UTF-8 BOM; `json.loads` chokes on a leading BOM (local
`ConvertFrom-Json` had tolerated it, masking the issue). Fixed on both sides:
re-stored the secret without a BOM (via .NET `UTF8Encoding($false)`), and
hardened `_load_secret_into_env` to strip a BOM, handle `SecretBinary`, and emit
clear errors. This is the reason that function is more defensive than a naive
`json.loads(get_secret_value()["SecretString"])`.

## Configuration reference (environment variables)

| Variable | Purpose | Default |
|----------|---------|---------|
| `NB_INSIGHTS_*` | Tableau credentials (server, api version, site, token) | from `.env` / secret |
| `NB_INSIGHTS_SECRET_ID` | Secrets Manager secret to load NB creds from | unset (local) |
| `NB_INSIGHTS_SECRET_REGION` | Region for that secret | `AWS_REGION` |
| `OUTPUT_DIR` | Where files are written | `.` (`/tmp` in Lambda) |
| `ZIP_MODE` | `delivery` or `physical` ZIP key | `delivery` |
| `ZIP_LOOKUP_S3_URI` | Fetch reference workbook from S3 | unset (local file) |
| `S3_BUCKET` / `S3_PREFIX` / `S3_REGION` | S3 upload target | unset (no upload) |
| `PRESIGN_EXPIRY_SECONDS` | Presigned URL lifetime | 604800 (7 days) |
| `SES_SENDER` / `SES_REGION` | Email sender + region | unset (no email) |
| `RECIPIENTS_S3_URI` / `RECIPIENTS_FILE` | Recipient list source | `recipients.txt` |
| `EMAIL_MESSAGE` | Free-text body message | CSV-vs-XLSX explanation |
| `NATIONBUILDER_SLUG` / `NATIONBUILDER_ACCESS_TOKEN` | NB V1 API creds (enable cross-check) | unset (skip) |
| `NB_LOOKUP_CONCURRENCY` | Parallel NB lookups | 10 |

## Security

- `.env` (token) and all report CSV/XLSX (member PII) are gitignored.
- `.env.example` documents variables with placeholders only.
- The status file (member names in its preview) is gitignored and untracked.
- The Lambda invoke response file (`aws/response.json`, contains presigned URLs
  with temporary tokens) is gitignored.
- S3 bucket has all Block Public Access settings on; sharing is via presigned
  URLs, never public policy.
- IAM is least-privilege and SES send is scoped to the one verified from-address.
