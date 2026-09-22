# Requirements — Zip / State / Federal District Validation

## Introduction

Democrats Abroad maintains member ("signup") data in NationBuilder. Reporting
is exposed through NationBuilder Insights, which is built on Tableau and
accessed via the Tableau REST API. This feature pulls the data behind the
"Zip State Federal District" Insights view and validates two data-integrity
concerns for every signup record:

1. Whether the member's registered ZIP code is consistent with their
   registered state.
2. Whether the member's assigned federal congressional district belongs to
   their registered state.

The output is a full annotated dataset plus a filtered file containing only the
records that failed one or both checks, so data stewards can review and correct
them.

## Glossary

- **Signup** — a member/person record, identified by `signup_id`.
- **Insights view** — a Tableau view (sheet) published in NationBuilder Insights.
- **ZIP lookup** — a reference workbook (`ZIP_Locale_Detail.xlsx`) mapping ZIP code to
  the correct state code.
- **Federal district prefix** (`fed_dist_prefix`) — the two-letter state code
  embedded in a federal district assignment (e.g. district `AK0` has prefix `AK`).

## Requirements

### Requirement 1 — Connect to NationBuilder Insights

**User Story:** As a data steward, I want the tool to authenticate to
NationBuilder Insights using a stored access token, so that credentials are not
hardcoded in source.

#### Acceptance Criteria

1. WHEN the tool starts THEN it SHALL load API credentials from a local `.env`
   file rather than from source code.
2. IF a required credential is missing THEN the tool SHALL fail with a clear
   message identifying the missing variable.
3. WHEN the tool finishes (success or failure) THEN it SHALL sign out of the
   Insights session.

### Requirement 2 — Pull the view data

**User Story:** As a data steward, I want to retrieve the underlying data of the
"Zip State Federal District" view, so that I can validate it.

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

### Requirement 3 — Problem-flag fields

**User Story:** As a data steward, I want two dedicated fields for the check
results, so that problems are explicit and machine-readable.

#### Acceptance Criteria

1. WHEN the output is produced THEN it SHALL include the fields
   `zip_state_problem` and `federal_district_problem`.
2. WHEN a record passes a check THEN the corresponding field SHALL be left blank.

### Requirement 4 — ZIP / state validation

**User Story:** As a data steward, I want each ZIP validated against its state,
so that I can find members whose ZIP does not belong to their stated state.

#### Acceptance Criteria

1. WHEN validating a record THEN the tool SHALL look up the ZIP in the ZIP lookup
   and compare the looked-up state to `registered_state`.
2. WHEN comparing ZIPs THEN the tool SHALL match ZIPs as integers so that
   leading-zero ZIPs (e.g. `01002`) compare consistently.
3. IF the looked-up state matches the registered state THEN `zip_state_problem`
   SHALL be blank.
4. IF the looked-up state differs THEN `zip_state_problem` SHALL be
   `ZIP STATE PROBLEM. SHOULD BE <state>`, where `<state>` is the state from the
   lookup.
5. IF the ZIP is blank or non-numeric THEN `zip_state_problem` SHALL be
   `ZIP STATE PROBLEM. ZIP IS BLANK`.
6. IF the ZIP is not present in the lookup THEN `zip_state_problem` SHALL be
   `ZIP STATE PROBLEM. ZIP NOT FOUND`.

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

### Requirement 6 — Output files

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
   Excel-friendly XLSX (ZIP column typed as text to preserve leading zeros).

### Requirement 7 — Run visibility

**User Story:** As a data steward, I want a summary of the run, so that I can see
how many records passed and failed without opening the CSVs.

#### Acceptance Criteria

1. WHEN a run completes THEN the tool SHALL report the row count, the count of
   passing vs. failing records for each check, and a breakdown of problem
   messages.
2. WHEN a run fails THEN the tool SHALL record the error so it can be diagnosed
   even if terminal output is unavailable.
