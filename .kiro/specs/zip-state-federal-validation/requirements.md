# Requirements — Zip / State / Federal District Validation

## Introduction

Democrats Abroad maintains member ("signup") data in NationBuilder. Reporting is
exposed through **NationBuilder Insights**, which is built on Tableau and
accessed via the Tableau REST API. This feature pulls the data behind the
"Zip State Federal District" Insights view and validates two data-integrity
concerns for every signup record:

1. Whether the member's registered ZIP code is consistent with their registered
   state.
2. Whether the member's assigned federal congressional district belongs to their
   registered state.

The output is a full annotated dataset plus, per test, a filtered file
containing only the records that failed that check, so data stewards can review
and correct them.

The feature began life as a set of local scripts and has since grown into a
scheduled AWS Lambda that stores its reports in S3 and emails download links to
a managed recipient list. This document captures both the original data-quality
requirements and the later operational requirements, and — importantly — the
**reasoning** behind each, so a future reader understands not just *what* the
system does but *why* it is shaped this way.

## Glossary

- **Signup** — a member/person record, identified by `signup_id`.
- **Insights view** — a Tableau view (sheet) published in NationBuilder Insights.
  The target view is "Zip State Federal District"
  (id `c7f541b2-87db-4fad-97c5-2e532dbbe956`).
- **ZIP reference** — the USPS `ZIP_Locale_Detail.xlsx` workbook mapping ZIP
  codes to the state that serves them. It replaced an earlier
  `zip_state_lookup.csv`.
- **Delivery ZIP / Physical ZIP** — two ZIP columns in the reference. Delivery
  ZIP is what a member writes on mail; physical ZIP is the ZIP of the post-office
  facility. The system keys on delivery ZIP by default (broader coverage).
- **Federal district prefix** (`fed_dist_prefix`) — the two-letter state code
  embedded in a federal district assignment (e.g. district `AK0` has prefix
  `AK`).
- **Presigned URL** — a time-limited, signed HTTPS link that lets someone
  download a private S3 object without AWS credentials.

## Requirements

Each requirement has a user story, EARS-style acceptance criteria, and a
**Rationale** note explaining why it exists / why it is done this way.

---

### Requirement 1 — Connect to NationBuilder Insights

**User Story:** As a data steward, I want the tool to authenticate to
NationBuilder Insights using a stored access token, so that credentials are not
hardcoded in source.

#### Acceptance Criteria

1. WHEN the tool starts THEN it SHALL load API credentials from the environment
   (a local `.env` file locally; see Requirement 10 for the AWS path) rather
   than from source code.
2. IF a required credential is missing THEN the tool SHALL fail with a clear
   message identifying the missing variable.
3. WHEN the tool finishes (success or failure) THEN it SHALL sign out of the
   Insights session.

**Rationale:** The Tableau personal access token is a secret; keeping it out of
source avoids leaking it through git history. Explicit "missing variable" errors
save debugging time, since a partially configured `.env` is the most common
first-run mistake. Guaranteed sign-out matters because a stale server session can
cause the next run's sign-out to fail — we hit exactly this when the API version
was mismatched.

---

### Requirement 2 — Pull the view data and remove PII

**User Story:** As a data steward, I want to retrieve the underlying data of the
"Zip State Federal District" view with member names removed, so that I can
validate it without spreading personal data.

#### Acceptance Criteria

1. WHEN the tool runs THEN it SHALL fetch the view data by the view's ID.
2. WHEN the data is retrieved THEN the tool SHALL drop personally identifying
   columns (`full_name`) before producing any output, keeping `signup_id` as a
   non-PII identifier for tracing records.
3. WHEN the data is retrieved THEN the tool SHALL order the columns as:
   `signup_id`, `registered_state`, `registered_zip_clean`, `federal_district`,
   followed by the two problem fields.
4. IF the view returns unexpected extra columns THEN the tool SHALL retain them
   rather than dropping data.

**Rationale:** `signup_id` is enough to look a record up in NationBuilder, so we
keep it and drop `full_name` — the reports still contain member ZIPs/states
(which is why they remain gitignored and are stored in a locked-down bucket), but
removing names reduces the sensitivity of every downstream artifact. The
"retain unexpected columns" rule is defensive: the Insights view schema can
change, and silently dropping a column could hide data the steward needs.

---

### Requirement 3 — Problem-flag fields

**User Story:** As a data steward, I want two dedicated fields for the check
results, so that problems are explicit and machine-readable.

#### Acceptance Criteria

1. WHEN the output is produced THEN it SHALL include the fields
   `zip_state_problem` and `federal_district_problem`.
2. WHEN a record passes a check THEN the corresponding field SHALL be left blank.

**Rationale:** Two separate columns (rather than one combined "issues" column)
let each test be filtered independently and let a record carry two distinct
problems at once. Blank-means-pass (rather than writing "OK") makes the
problems-only reports trivial to define — "any row where this flag is non-blank"
— and makes populated cells easy to spot when scanning the full file.

---

### Requirement 4 — ZIP / state validation

**User Story:** As a data steward, I want each ZIP validated against its state,
so that I can find members whose ZIP does not belong to their stated state.

#### Acceptance Criteria

1. WHEN validating a record THEN the tool SHALL look up the ZIP in the ZIP
   reference and compare the looked-up state to `registered_state`.
2. WHEN comparing ZIPs THEN the tool SHALL match ZIPs as integers so that
   leading-zero ZIPs (e.g. `01002`) compare consistently.
3. IF the looked-up state matches the registered state THEN `zip_state_problem`
   SHALL be blank.
4. IF the looked-up state differs THEN `zip_state_problem` SHALL be
   `ZIP STATE PROBLEM. SHOULD BE <state>`, where `<state>` is the state from the
   reference.
5. IF the ZIP is blank or non-numeric THEN `zip_state_problem` SHALL be
   `ZIP STATE PROBLEM. ZIP IS BLANK`.
6. IF the ZIP is not present in the reference THEN `zip_state_problem` SHALL be
   `ZIP STATE PROBLEM. ZIP NOT FOUND`.

**Rationale:** Integer matching is the key trick. Both the Insights view and the
reference store ZIPs as numbers (`1002`, not `01002`), so coercing both sides to
integers makes leading-zero ZIPs compare correctly without any string padding.
The explicit `SHOULD BE <state>` message tells the steward the *correct* state
directly, turning a flag into an actionable fix. `ZIP NOT FOUND` is kept as a
distinct outcome (rather than folded into a state mismatch) because a ZIP absent
from the reference is a different kind of problem — after investigation these
turned out to be placeholder/unassigned ZIPs, not non-US/APO ZIPs, so a generic
"not found" is the honest label.

---

### Requirement 5 — Federal district validation

**User Story:** As a data steward, I want each federal district validated against
the member's state, so that I can find members assigned to a district in the
wrong state.

#### Acceptance Criteria

1. WHEN validating a record THEN the tool SHALL compare the federal district
   prefix (`fed_dist_prefix`) to `registered_state`.
2. IF the prefix matches the registered state THEN `federal_district_problem`
   SHALL be blank.
3. IF the prefix differs from the registered state THEN
   `federal_district_problem` SHALL be `FEDERAL DISTRICT PROBLEM`.
4. IF the federal district / prefix is blank THEN `federal_district_problem`
   SHALL be `FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK`.
5. WHEN the output files are written THEN the `fed_dist_prefix` column SHALL be
   used for the test but SHALL NOT appear in either output file.

**Rationale:** This check does not need the ZIP reference at all — the state is
already embedded in the district code, so a self-contained prefix-vs-state
comparison is sufficient and cheap. `fed_dist_prefix` is an intermediate value
only, so it is dropped from output to avoid confusing the steward with a column
that duplicates state information.

---

### Requirement 6 — Output files (full dataset + per-test reports)

**User Story:** As a data steward, I want a full annotated file plus a separate
problems report for each test, so that I can review everything or focus on the
failures of one specific check at a time.

#### Acceptance Criteria

1. WHEN validation completes THEN the tool SHALL write a full file of all records
   with both problem fields populated.
2. WHEN validation completes THEN the tool SHALL write a separate problems report
   per test: one containing only records where `zip_state_problem` is non-blank,
   and one containing only records where `federal_district_problem` is non-blank.
3. WHEN a per-test problems report is written THEN it SHALL carry that test's
   flag column and exclude the other test's flag column (and `fed_dist_prefix`).
4. WHEN any output file is written THEN it SHALL be produced as both a CSV and an
   Excel-friendly XLSX.

**Rationale:** The two checks are reviewed by different concerns and at different
cadences, so splitting them into separate reports (rather than one combined
"problems" file) lets a steward focus on one test at a time. The full file is
retained for anyone who wants both flags side by side. Both CSV and XLSX are
produced because of the leading-zero ZIP problem — see Requirement 6a.

---

### Requirement 6a — Preserve leading-zero ZIPs in output

**User Story:** As a data steward opening a report, I want ZIP codes to keep
their leading zeros in whatever tool I use, so that `01002` does not become
`1002`.

#### Acceptance Criteria

1. WHEN a CSV is written THEN the ZIP column SHALL be stored as zero-padded
   5-digit text (e.g. `01002`).
2. WHEN an XLSX is written THEN the ZIP column SHALL be typed as Excel text
   ("@" number format) so Excel preserves the leading zero on open.

**Rationale:** A plain CSV shows `01002` correctly in text editors, Google
Sheets, and pandas, but Excel silently coerces it to the number `1002` on
double-click. Typing the XLSX column as text is the only way Excel keeps the
zero without a manual import step. Providing both formats means every consumer
gets a correct view — this is the exact reason the notification email carries an
explanatory message (Requirement 11).

---

### Requirement 7 — Run visibility

**User Story:** As a data steward, I want a summary of the run, so that I can see
how many records passed and failed without opening the reports.

#### Acceptance Criteria

1. WHEN a run completes THEN the tool SHALL report the row count, the count of
   passing vs. failing records for each check, and a breakdown of problem
   messages.
2. WHEN a run fails THEN the tool SHALL record the error (with traceback) so it
   can be diagnosed even if terminal output is unavailable.
3. WHEN run as a Lambda THEN the tool SHALL return a JSON-serializable summary
   (status, counts, files, presigned URLs) as its response.

**Rationale:** During development the workspace lived on a drive whose shell
intermittently mangled command output, so a written status file became the
reliable record of each run. The same summary, returned as a structured Lambda
response, lets the scheduler/logs (and a human reading CloudWatch) confirm
success and see the counts without opening any file.

---

### Requirement 8 — ZIP reference workbook (three-sheet merge)

**User Story:** As a data steward, I want ZIP validation to use an authoritative,
comprehensive USPS reference, so that valid ZIPs are not wrongly flagged as
unknown.

#### Acceptance Criteria

1. WHEN building the ZIP→state lookup THEN the tool SHALL read the USPS
   `ZIP_Locale_Detail.xlsx` workbook.
2. WHEN building the lookup THEN the tool SHALL merge all three sheets — `Detail`,
   `Unique`, and `Other` — into a single ZIP→state map, with `Detail` taking
   precedence on conflicts.
3. WHEN reading `Unique` and `Other` THEN the tool SHALL account for their
   3-row stacked header (the real header is the third row; the split
   "PHYSICAL STATE"/"PHYSICAL ZIP" labels collapse to `STATE`/`ZIP` and the
   delivery-ZIP column is named `ZIPCODE`).
4. WHEN keying the lookup THEN the tool SHALL use the delivery ZIP by default,
   configurable to the physical ZIP via `ZIP_MODE`.

**Rationale:** The original single-CSV reference was replaced to improve
coverage and accuracy. The `Detail` sheet alone omits ~195 valid ZIPs
(universities, PO-box-only, single-entity ZIPs such as `00544` Holtsville and
`08544` Princeton); those live on `Unique`/`Other`. Merging all three drops the
false "ZIP NOT FOUND" count dramatically. Delivery ZIP is the default because it
matches what members actually enter and covers ~37K ZIPs vs. ~29K for physical
ZIP; the physical mode is retained as an option. Full detail lives in
`ZIP_REFERENCE_AND_ERRORS.md`.

---

### Requirement 9 — S3 output and presigned download links

**User Story:** As a data steward, I want the reports delivered to durable,
access-controlled storage with shareable links, so that consumers can download
them without direct file access.

#### Acceptance Criteria

1. WHEN `S3_BUCKET` is configured THEN the tool SHALL upload each output file to
   `s3://<bucket>/<prefix>/<name>`.
2. WHEN uploading THEN the tool SHALL date-stamp each S3 object name with the run
   date in `YYYYMMDD` format (e.g. `zip_state_problems_20260929.csv`), so a
   history of runs accumulates in the bucket. Local file names remain undated.
3. WHEN a file is uploaded THEN the tool SHALL generate a presigned GET URL for
   it, with a configurable expiry (`PRESIGN_EXPIRY_SECONDS`, default 7 days —
   S3's maximum), retained in the run result for reference.
4. WHEN `S3_BUCKET` is unset THEN the tool SHALL write files locally only and
   perform no upload (so local runs are unaffected).
5. WHEN the bucket is created THEN it SHALL have all S3 Block Public Access
   settings enabled (the reports contain member PII).

**Rationale:** S3 is the natural sink for a Lambda and needs no credentials
beyond the execution role. Date-stamping builds a durable history — each nightly
run leaves its own dated objects rather than overwriting, so past reports can be
compared. Presigned URLs are still generated (kept in the result, and used by the
retained link-email alternative). Block Public Access is mandatory because the
files contain member ZIPs/states; the public web must never be able to read them.
Making the whole feature a no-op when `S3_BUCKET` is unset keeps the same code
runnable on a laptop.

---

### Requirement 10 — Runnable as a scheduled AWS Lambda

**User Story:** As an operator, I want the validation to run automatically in AWS
on a schedule, so that fresh reports are produced without anyone running a script.

#### Acceptance Criteria

1. WHEN deployed THEN the tool SHALL expose a Lambda handler
   (`pull_zip_state_federal.handler`) that runs the same pipeline as the local
   entry point and returns the run summary.
2. WHEN running in Lambda THEN the tool SHALL write outputs to a writable path
   (`OUTPUT_DIR=/tmp`) and MAY source the ZIP reference workbook from S3
   (`ZIP_LOOKUP_S3_URI`) rather than bundling it.
3. WHEN `NB_INSIGHTS_SECRET_ID` is set THEN the tool SHALL load NationBuilder
   credentials from AWS Secrets Manager, without overwriting any variable already
   present in the environment.
4. WHEN packaged THEN the deployment SHALL exclude pandas/numpy (provided by the
   AWS-managed pandas layer) and SHALL run on schedule via EventBridge Scheduler.

**Rationale:** A Lambda removes the need for anyone to run the script manually.
`/tmp` is the only writable path in Lambda, so `OUTPUT_DIR` is configurable.
Sourcing the ~4 MB reference from S3 keeps the deployment package tiny and lets
the reference be updated without a redeploy. Secrets Manager (rather than plain
function env vars) protects the token secret; the "don't overwrite existing env"
rule means the same module works locally (`.env` wins) and in Lambda (secret
fills the gap). The pandas layer avoids shipping/compiling pandas and keeps the
zip ~3 MB.

---

### Requirement 11 — Email notification with download links

**User Story:** As a data steward, I want the report links emailed to me
automatically, so that I don't have to go looking for the output after each run.

#### Acceptance Criteria

1. WHEN `SES_SENDER` is set and a recipient list is available THEN the tool SHALL
   email the problem reports as **file attachments** via Amazon SES after the run
   (using a raw MIME message).
2. WHEN emailing THEN the tool SHALL include the summary counts and a
   configurable free-text message (`EMAIL_MESSAGE`) in the body.
3. WHEN emailing THEN the tool SHALL attach the **problem reports only**; the full
   dataset files SHALL be uploaded to S3 but NOT attached.
4. WHEN emailing THEN the body SHALL contain counts and a message only — never
   inline member data — and attachments SHALL be named with the run date.
5. WHEN an attachment would push the message past the SES size limit (~10 MB)
   THEN the tool SHALL skip it and note the omission in the body rather than fail.
6. WHEN the recipient list is read THEN it SHALL come from a plain-text file (one
   address per line, `#` comments) sourced from S3 (`RECIPIENTS_S3_URI`) or a
   local file (`RECIPIENTS_FILE`), so recipients can be managed without a
   redeploy.
7. WHEN `SES_SENDER` is unset or the recipient list is empty THEN the tool SHALL
   send no email (no-op). Email SHALL NOT require S3 (attachments come from the
   local output files).

**Rationale:** Attachments let recipients open the reports directly from their
inbox, with no link to click or expiry to worry about — simpler for the small,
known audience. Only the problem reports are attached: the full dataset is large
(and would exceed the SES 10 MB limit), so it is uploaded to S3 for reference but
never attached. The size guard means a future data spike degrades gracefully
(skip + note) instead of failing the send. The recipient list stays an S3 text
file so it can grow by editing one object. The configurable `EMAIL_MESSAGE`
currently explains why both CSV and XLSX are provided (the leading-zero ZIP issue
from Requirement 6a).

**Alternative retained:** an earlier version emailed presigned S3 *links* instead
of attachments. That path is preserved (unused) as `_build_link_email()` for an
easy switch back.

---

### Requirement 12 — NationBuilder federal-district cross-check

**User Story:** As a data steward, I want the federal district of each flagged
record checked against the authoritative NationBuilder value, so that I can see
where the (unreliable) Insights value is wrong rather than trusting it blindly.

#### Acceptance Criteria

1. WHEN `NATIONBUILDER_SLUG` and `NATIONBUILDER_ACCESS_TOKEN` are set THEN the
   tool SHALL, for each record flagged by the federal-district test, look up that
   `signup_id`'s `federal_district` via the NationBuilder V1 API.
2. WHEN the cross-check runs THEN the federal-district report SHALL gain two
   columns: `nb_federal_district` (the NB value, or a marker: blank when NB has
   none, `NOT FOUND` when the id is absent, `LOOKUP ERROR` on failure) and
   `federal_district_match` (`MATCH`/`MISMATCH`, or blank when not comparable).
3. WHEN querying NB THEN the tool SHALL only call the API for the flagged
   `signup_id`s (not the whole nation), one call per id, run concurrently
   (`NB_LOOKUP_CONCURRENCY`, default 10). The NB values are the member's
   `registered_address.state` and `registered_address.zip` (which correspond to
   the Insights "registered" values) and the top-level `federal_district`.
4. WHEN NB credentials are unset THEN the cross-check SHALL be skipped and the
   reports produced exactly as before.
5. WHEN a lookup fails or an id is missing THEN the tool SHALL record a marker
   and continue rather than failing the run.
6. WHEN the cross-check runs THEN the tool SHALL also produce a third report,
   `insights_vs_nationbuilder.*`, over the **union of all flagged records**
   (anything flagged by either test), comparing `state`, `zip`, and
   `federal_district` between Insights and NB with a per-field match flag.
7. WHEN building the third report THEN ZIPs SHALL be compared normalized (first
   five digits, zero-padded) so `01002`, `1002`, and `01002-1234` compare
   equivalently, and the report SHALL keep ONLY records where at least one of
   the three fields genuinely differs (a field NB cannot supply is "not
   comparable" and does not by itself count as a difference).
8. WHEN the third report is produced THEN it SHALL be delivered the same way as
   the other reports (date-stamped S3 object + email attachment).

**Rationale:** The Insights `federal_district` is unreliable, but NationBuilder
holds an authoritative per-person value exposed on `/api/v1/people`. Showing both
side by side (option b, not replacing one with the other) lets a steward judge
each case — early testing found many records where Insights was blank or wrong
while NB had the correct district (e.g. Insights `AK0` vs NB `PA6` for a PA
member). Only the flagged ids are queried to keep the call volume bounded
(~1,000, not ~188K); concurrency keeps that under the Lambda timeout. The client
is vendored from the `NBGet01` project; it uses a simple access-token V1 API call
over `httpx`. Credentials live in the same Secrets Manager secret as the Insights
ones in AWS.

---

## Non-goals / explicit decisions

- **No Google Drive delivery.** Considered, but the destination was a personal
  "Shared with Me" folder and the app was heading to AWS anyway; S3 + presigned
  URLs is simpler and avoids the service-account storage-quota problem.
- **No public S3 access.** Sharing is via presigned URLs (and, later, IAM /
  cross-account if needed), never a public bucket policy.
- **No container image in production (for now).** A `Dockerfile` exists as a
  fallback, but the zip + managed pandas layer is the deployed path.
