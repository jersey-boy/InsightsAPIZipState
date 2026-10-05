# Requirements — Zip / State / Federal District Validation (NationBuilder V2)

## Introduction

Democrats Abroad maintains member ("signup") data in NationBuilder. This feature
pulls every signup from the **NationBuilder V2 API** (OAuth 2.0) and validates
two data-integrity concerns per record:

1. Whether the member's registered ZIP code is consistent with their registered
   state.
2. Whether the member's assigned federal congressional district belongs to their
   registered state.

The output is a full annotated dataset plus, per test, a filtered file of only
the records that failed that check, so data stewards can review and correct them.

NationBuilder V2 is the **sole** data source. An earlier version read from
NationBuilder Insights (Tableau) and the NationBuilder V1 API; that code has been
removed. This document reflects the current V2-only system, with the reasoning
behind each requirement.

## Glossary

- **Signup** — a member/person record, identified by `signup_id` (the V2
  `data[].id`).
- **NationBuilder V2 API** — the current REST API (`/api/v2/...`), authenticated
  with OAuth 2.0 bearer tokens, returning JSON:API-shaped responses.
- **registered_address** — the member's US voter-registration address (holds the
  state and ZIP we validate). Distinct from `primary_address`, which for DA
  members is usually overseas.
- **ZIP reference** — the USPS `ZIP_Locale_Detail.xlsx` workbook mapping ZIP code
  to the state that serves it.
- **Federal district prefix** — the two-letter state code at the start of a
  federal district (e.g. `NY13` → `NY`).

## Requirements

### Requirement 1 — Authenticate to NationBuilder V2 (OAuth 2.0)

**User Story:** As a data steward, I want the tool to authenticate to the
NationBuilder V2 API using OAuth 2.0, so that it can read signup data securely
without a human in the loop on every run.

#### Acceptance Criteria

1. WHEN the tool runs THEN it SHALL obtain a V2 access token using the OAuth 2.0
   refresh-token flow (client id + client secret + refresh token).
2. WHEN the access token is refreshed THEN the tool SHALL persist the rotated
   refresh token durably (Secrets Manager in Lambda; `.env` locally) so the next
   run can authenticate.
3. WHEN credentials are missing THEN the tool SHALL fail with a clear error
   naming the missing variable.

**Rationale:** V2 access tokens expire after 24h, so a durable refresh token (and
persisting its rotation) is what makes an unattended scheduled job possible. See
`V2_OAUTH_SETUP.md`.

### Requirement 2 — Pull all signups and project the needed fields

**User Story:** As a data steward, I want all signups pulled from V2 with the
registered address and the fields the checks and filter need.

#### Acceptance Criteria

1. WHEN pulling THEN the tool SHALL request
   `/api/v2/signups?extra_fields[signups]=registered_address,custom_values` and
   page through all signups.
2. WHEN projecting a record THEN the tool SHALL extract: `signup_id`,
   `registered_state` and `registered_zip` (from `registered_address`),
   `federal_district` (top-level attribute), and `us_citizen` +
   `date_last_verified` (from `custom_values`).
3. WHEN reading the ZIP THEN the tool SHALL normalize it to its first five digits
   (handling ZIP+4 and blanks).

**Rationale:** `us_citizen` and `date_last_verified` are NationBuilder custom
fields (inside `custom_values`), not top-level attributes, so they must be
requested explicitly. `registered_address` — not `primary_address` — carries the
US state/ZIP being validated.

### Requirement 3 — Full-pull performance

**User Story:** As an operator, I want the full-nation pull to complete well
within the Lambda time and memory limits.

#### Acceptance Criteria

1. WHEN pulling THEN the tool SHALL fetch pages in parallel (V2 uses page-number
   pagination, so pages can be requested directly), with a configurable
   concurrency (`NB_LOOKUP_CONCURRENCY`, default 20).
2. WHEN a page request is rate-limited (429) or hits a transient 5xx THEN the
   tool SHALL retry with backoff.
3. WHEN deployed THEN the function SHALL be provisioned with enough memory to
   hold the full pull in memory (3008 MB) and a 900 s timeout.

**Rationale:** V2 caps `page[size]` at 100 (~1,886 pages for ~188k signups).
Serial paging would take ~19 min (over the Lambda max); parallel paging brings it
to ~4–5 min. The full record set plus a DataFrame needs ~3 GB — at 1024 MB the
function is killed with `Runtime.OutOfMemory`.

### Requirement 4 — Inclusion filter

**User Story:** As a data steward, I want validation limited to the population
that should be checked, excluding records that cannot meaningfully be validated.

#### Acceptance Criteria

1. WHEN filtering THEN the tool SHALL exclude records with a blank/null
   registered ZIP.
2. WHEN filtering THEN the tool SHALL include only records where `us_citizen` is
   truthy.
3. WHEN filtering THEN the tool SHALL exclude records with a blank/null
   `date_last_verified`.
4. WHEN filtering completes THEN the tool SHALL report how many records were kept
   and how many were excluded by each reason.

**Rationale:** A record with no US ZIP, a non-citizen, or an unverified record
isn't a meaningful ZIP/state/district validation target. `us_citizen` is returned
by V2 as a boolean; the filter accepts the common truthy encodings defensively.

### Requirement 5 — ZIP / state validation

**User Story:** As a data steward, I want each ZIP validated against its state.

#### Acceptance Criteria

1. WHEN validating THEN the tool SHALL look up the registered ZIP in the ZIP
   reference and compare the looked-up state to `registered_state`.
2. WHEN comparing ZIPs THEN the tool SHALL match as integers so leading-zero ZIPs
   compare consistently.
3. IF the looked-up state matches THEN `zip_state_problem` SHALL be blank.
4. IF it differs THEN `zip_state_problem` SHALL be
   `ZIP STATE PROBLEM. SHOULD BE <state>`.
5. IF the ZIP is blank/non-numeric THEN it SHALL be
   `ZIP STATE PROBLEM. ZIP IS BLANK`.
6. IF the ZIP is not in the reference THEN it SHALL be
   `ZIP STATE PROBLEM. ZIP NOT FOUND`.

### Requirement 6 — Federal district validation

**User Story:** As a data steward, I want each federal district validated against
the member's state.

#### Acceptance Criteria

1. WHEN validating THEN the tool SHALL compare the federal district's two-letter
   prefix (e.g. `NY` from `NY13`) to `registered_state`.
2. IF the prefix matches THEN `federal_district_problem` SHALL be blank.
3. IF it differs THEN it SHALL be `FEDERAL DISTRICT PROBLEM`.
4. IF the federal district is blank THEN it SHALL be
   `FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK`.

**Rationale:** The state is embedded in the district code itself, so a
prefix-vs-state comparison needs no external reference.

### Requirement 7 — Output files

**User Story:** As a data steward, I want a full annotated file plus a separate
problems report per test.

#### Acceptance Criteria

1. WHEN validation completes THEN the tool SHALL write a full file of all kept
   records with both problem-flag columns.
2. WHEN validation completes THEN the tool SHALL write a problems report per test
   (ZIP/state and federal district), each carrying only its own flag column.
3. WHEN any output file is written THEN it SHALL be produced as both a CSV and an
   Excel-friendly XLSX (ZIP column typed as text to preserve leading zeros).

### Requirement 8 — ZIP reference workbook

**User Story:** As a data steward, I want ZIP validation to use an authoritative,
comprehensive USPS reference.

#### Acceptance Criteria

1. WHEN building the lookup THEN the tool SHALL merge all three sheets of
   `ZIP_Locale_Detail.xlsx` (`Detail`, `Unique`, `Other`), with `Detail` winning
   on conflicts, handling the stacked headers on `Unique`/`Other`.
2. WHEN keying the lookup THEN the tool SHALL use the delivery ZIP by default,
   configurable to the physical ZIP via `ZIP_MODE`.
3. WHEN running in Lambda THEN the workbook MAY be sourced from S3 via
   `ZIP_LOOKUP_S3_URI`.

**Rationale:** `Detail` alone omits ~195 valid ZIPs (universities, PO-box-only,
single-entity) that live on `Unique`/`Other`. See `ZIP_REFERENCE_AND_ERRORS.md`.

### Requirement 9 — S3 output with date-stamped history

**User Story:** As a data steward, I want reports delivered to durable storage,
accumulating a history.

#### Acceptance Criteria

1. WHEN `S3_BUCKET` is configured THEN the tool SHALL upload each output file to
   `s3://<bucket>/<prefix>/<name>` with the object name date-stamped `YYYYMMDD`
   (local file names stay undated).
2. WHEN uploading THEN the tool SHALL also generate a presigned GET URL per
   object (retained in the run result).
3. WHEN `S3_BUCKET` is unset THEN output SHALL stay local only.
4. WHEN the bucket is created THEN it SHALL have all Block Public Access settings
   enabled (member PII).

### Requirement 10 — Scheduled AWS Lambda

**User Story:** As an operator, I want the validation to run automatically on a
schedule.

#### Acceptance Criteria

1. WHEN deployed THEN the tool SHALL expose `validate_zip_fed.handler`, writing to
   `/tmp` and sourcing the reference workbook from S3.
2. WHEN running in Lambda THEN NationBuilder OAuth credentials SHALL be loaded
   from Secrets Manager (`NATIONBUILDER_SECRET_ID`), with the rotated refresh
   token written back to that secret.
3. WHEN packaged THEN the deployment SHALL exclude pandas/numpy (from the managed
   layer) and run on an EventBridge schedule.

### Requirement 11 — Email notification with attachments

**User Story:** As a data steward, I want the problem reports emailed to me.

#### Acceptance Criteria

1. WHEN `SES_SENDER` is set and a recipient list exists THEN the tool SHALL email
   the two problem reports as file attachments (dated names) via Amazon SES.
2. WHEN emailing THEN the full dataset SHALL NOT be attached (it is in S3), and an
   attachment that would breach the SES ~10 MB limit SHALL be skipped with a note.
3. WHEN emailing THEN the body SHALL carry counts and a configurable
   `EMAIL_MESSAGE` only — never inline member data.
4. WHEN the recipient list is read THEN it SHALL come from a text file (S3 via
   `RECIPIENTS_S3_URI` or local `RECIPIENTS_FILE`), editable without a redeploy.
5. WHEN `SES_SENDER` is unset THEN no email SHALL be sent.

### Requirement 12 — Run visibility

**User Story:** As a data steward, I want a summary of each run.

#### Acceptance Criteria

1. WHEN a run completes THEN the tool SHALL report counts (kept, per-test
   problems, filter exclusions) to a status file and stdout.
2. WHEN run as a Lambda THEN it SHALL return a JSON-serializable summary (status,
   counts, files, presigned URLs, emailed recipients).
3. WHEN a run fails THEN it SHALL record the error (with traceback) and return
   `statusCode: 500`.

## Non-goals / explicit decisions

- **No Insights/Tableau or V1 source.** V2 is the only source; that legacy code
  was removed.
- **No upstream filtering of the pull.** All signups are pulled, then filtered
  downstream (keeps the pull simple and the filter logic in one place).
- **No public S3 access.** Sharing is via email attachments / presigned URLs,
  never a public bucket.
