# Chat02 — Session Log

A record of the work done in this session on the `InsightsAPIZipState` project
(local workspace `d:\Kiro\ZipStateFed`, GitHub `jersey-boy/InsightsAPIZipState`).

> Note: this is a faithful summary written from the session, not a verbatim
> transcript.

---

## 1. Clone and local setup

- Cloned `https://github.com/jersey-boy/InsightsAPIZipState` into the workspace.
- Created a Python 3.12 virtualenv (`.venv`) and installed `requirements.txt`
  (tableau-api-lib, pandas, python-dotenv, openpyxl).
- Copied `.env.example` to `.env`.
- Confirmed the project pulls a NationBuilder Insights (Tableau) view, validates
  ZIP vs. state and federal district vs. state, and writes CSV/XLSX reports.

## 2. First runs and credentials

- Initial sign-in smoke test surfaced two issues: the `.env` still had
  placeholder values, and the API version was 3.21 while the server is on 3.23.
- After the correct `.env` was supplied (site `democratsabroad`, API 3.23),
  sign-in and the full pull succeeded.
- Mac -> Windows portability: code was portable; only the README run command
  differed (`.venv\Scripts\python.exe` on Windows).

## 3. New reference file: ZIP_Locale_Detail.xlsx

- Switched the ZIP -> state reference from `zip_state_lookup.csv` to the USPS
  `ZIP_Locale_Detail.xlsx` workbook.
- The workbook has three sheets: `Detail` (clean header), `Unique` and `Other`
  (3-row stacked header; columns collapse to `ZIPCODE`/`STATE`/`ZIP`).
- Started with **physical** ZIP/state, which produced ~33,570 "ZIP NOT FOUND"
  (a coverage artifact), then switched to **delivery** ZIP per the plan.
- Investigated the remaining "ZIP NOT FOUND" cases: they were NOT non-US/APO —
  they were valid US ZIPs missing from the `Detail` sheet. Merging `Unique` +
  `Other` recovered ~195 of them. Decided to keep the `ZIP NOT FOUND` message
  (the ~50 remaining are placeholder/unassigned ZIPs, not "non-US").
- Result in delivery mode with all three sheets merged: ~90 ZIP/state problems,
  ~976 federal district problems.

## 4. Documentation and report split

- Wrote `ZIP_REFERENCE_AND_ERRORS.md` explaining the three-sheet merge, the
  physical/delivery `ZIP_MODE` switch, and what every error message means.
- Split the single combined problems file into two per-test reports:
  `zip_state_problems.*` and `federal_district_problems.*` (each keeps only its
  own flag column). The full dataset file still carries both flags.
- Updated README and `.kiro/specs/*` to match.
- Removed `full_name` (PII) from all outputs; kept `signup_id` as a non-PII
  identifier.

## 5. Git hygiene along the way

- Restored `.env.example` after it had been accidentally overwritten with real
  credentials (prevented a secret leak).
- Untracked `pull_zip_state_federal.status.txt` (contains member names) and
  gitignored it.

## 6. S3 output + presigned URLs

- Added `OUTPUT_DIR` (default `.`, `/tmp` in Lambda).
- Added optional S3 upload: when `S3_BUCKET` is set, each output file is
  uploaded and a presigned URL is generated (`PRESIGN_EXPIRY_SECONDS`, default
  7 days). No-op locally when unset. Added `boto3` to requirements.

## 7. Lambda-ready refactor

- Added `handler(event, context)`; refactored `main()` into `run()` returning a
  result dict (status, counts, files, presigned URLs).
- `nb_insights.py`: load credentials from AWS Secrets Manager when
  `NB_INSIGHTS_SECRET_ID` is set (env/.env still wins locally).
- Reference workbook can be fetched from S3 at runtime via `ZIP_LOOKUP_S3_URI`.
- Packaging decision: **zip + AWS-managed pandas layer** as primary (Docker as a
  fallback; `Dockerfile`/`.dockerignore` kept). `build_lambda_zip.py` builds a
  ~2.8 MB zip of Linux wheels, pruning pandas/numpy (from the layer).
- Wrote `DEPLOY.md`.

## 8. AWS deploy (account 068238656047, region eu-west-2, profile zipstatefed)

- S3 bucket `zipstatefed-reports-068238656047` with all Block Public Access
  settings on.
- Uploaded `ZIP_Locale_Detail.xlsx` to `refs/`.
- Secrets Manager secret `nb/insights/prod` (built from local `.env`).
  - Hit a bug: PowerShell `Set-Content -Encoding utf8` wrote a UTF-8 BOM into
    the secret, breaking `json.loads`. Fixed by re-storing without a BOM AND
    hardening `_load_secret_into_env` (strip BOM, handle SecretBinary, clear
    errors).
- IAM execution role `zipstatefed-lambda-role` (least-privilege: logs, secret
  read, S3 Get/Put, later SES send scoped to the from-address).
- Pandas layer for eu-west-2/py3.12:
  `arn:aws:lambda:eu-west-2:336392948345:layer:AWSSDKPandas-Python312:31`.
- Created function `zipstatefed-validation` (python3.12, 1024 MB, 300 s).
- Verified invoke: 200 OK, ~178,073 rows, six files in S3, presigned URLs
  returned.
- EventBridge Scheduler `zipstatefed-nightly` (cron `0 6 * * ? *` UTC) with a
  dedicated invoke role.

## 9. Email notifications (Amazon SES)

- Verified the `lategothikdata.com` domain in SES via DKIM CNAMEs added to its
  Route 53 hosted zone. Sender: `insights-reports@lategothikdata.com`.
- SES is in the sandbox, so verified the recipient `rick_meier@msn.com` too.
- Recipient list in S3 (`config/recipients.txt`), one address per line.
- `notify_by_email()` sends the summary counts + presigned links via SES.
- Later refined: email links to the **problem reports only** (full dataset still
  uploaded to S3 but not linked), and added a configurable `EMAIL_MESSAGE` body
  message defaulting to an explanation of why both CSV and XLSX are provided
  (the leading-zero ZIP issue).
- Verified live: 200 OK, `emailed_to: rick_meier@msn.com`.

## 10. Commits pushed to origin/main (in order)

1. Split validation into two per-test reports; document ZIP reference
2. Drop full_name (PII) from all output files
3. Add configurable OUTPUT_DIR and optional S3 upload with presigned URLs
4. Make the validation runnable as an AWS Lambda
5. Harden Secrets Manager loading; add AWS deploy artifacts
6. Email presigned report links via SES to a managed recipient list
7. Email only problem-report links + add configurable body message

## 11. Open follow-ups (no action taken yet)

- Request SES **production access** before adding recipients that can't be
  individually verified.
- **Rotate the NationBuilder token** (lives in local `.env` and Secrets
  Manager).
- First emails from the new domain may land in spam until reputation builds.

## Final architecture

Nightly at 06:00 UTC, the Lambda: pulls the Insights view -> drops PII ->
validates ZIP/state and federal district/state (ZIP reference merged from the
three workbook sheets) -> writes six CSV/XLSX reports to `/tmp` -> uploads them
to the locked-down S3 bucket -> emails the problem-report presigned links (plus
counts and the CSV/XLSX explanation) to the managed recipient list.
