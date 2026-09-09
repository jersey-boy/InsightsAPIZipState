# Tasks — Zip / State / Federal District Validation

Implementation plan. Checked items are already built and verified against the
live Insights site (176,493 records pulled; 75 ZIP/state problems, 944 federal
district problems, 1,010 combined problem rows).

- [x] 1. Project scaffolding and secrets handling
  - Create `.env`, `.env.example`, `.gitignore`, `requirements.txt`.
  - Ensure `.env` and output CSVs are gitignored.
  - _Requirements: 1.1_

- [x] 2. Shared connection module (`nb_insights.py`)
  - [x] 2.1 Load `.env` and build the `tableau_api_lib` config from env vars.
    - _Requirements: 1.1_
  - [x] 2.2 Add `_require()` to raise `MissingCredentialsError` on missing vars.
    - _Requirements: 1.2_
  - [x] 2.3 Provide `insights_connection()` context manager (sign in / sign out).
    - _Requirements: 1.3_

- [x] 3. Content inventory app (`list_reports.py`)
  - List workbooks, views, and data sources; write each to CSV.
  - Used to locate the target view ID.
  - _Requirements: (supporting)_

- [x] 4. Pull the view data (`pull_zip_state_federal.py`)
  - [x] 4.1 Fetch view data by ID (`c7f541b2-87db-4fad-97c5-2e532dbbe956`).
    - _Requirements: 2.1_
  - [x] 4.2 Reorder columns; append any unexpected columns to avoid data loss.
    - _Requirements: 2.2, 2.3_
  - [x] 4.3 Add blank `zip_state_problem` and `federal_district_problem` fields.
    - _Requirements: 3.1, 3.2_

- [x] 5. ZIP / state validation
  - [x] 5.1 Build integer-keyed `zip -> state` lookup from `zip_state_lookup.csv`.
    - _Requirements: 4.1, 4.2_
  - [x] 5.2 Compute result with precedence: blank ZIP, not found, match (blank),
        mismatch (`SHOULD BE <state>`).
    - _Requirements: 4.3, 4.4, 4.5, 4.6_

- [x] 6. Federal district validation
  - [x] 6.1 Compare `fed_dist_prefix` to `registered_state`.
    - _Requirements: 5.1, 5.2, 5.3_
  - [x] 6.2 Flag blank prefix with `FEDERAL DISTRICT IS BLANK` message.
    - _Requirements: 5.4_
  - [x] 6.3 Drop `fed_dist_prefix` before writing outputs.
    - _Requirements: 5.5_

- [x] 7. Output files
  - [x] 7.1 Write full annotated CSV (`zip_state_federal_district.csv`).
    - _Requirements: 6.1, 6.3_
  - [x] 7.2 Write problems-only CSV (`zip_state_federal_district_problems.csv`).
    - _Requirements: 6.2, 6.3_

- [x] 8. Run visibility
  - [x] 8.1 Print/save row counts, pass/fail counts, and problem breakdowns.
    - _Requirements: 7.1_
  - [x] 8.2 Write `STATUS: OK` / `STATUS: ERROR` (+ traceback) to a status file.
    - _Requirements: 7.2_

## Open / follow-up tasks

- [ ] 9. Make the target view configurable
  - Move `VIEW_ID` and output filenames to CLI args or `.env` so the same tool
    can validate other views without editing source.

- [ ] 10. Optional ZIP formatting
  - Add an option to zero-pad `registered_zip_clean` to 5 digits in the output
    (display concern only; the integer match already handles comparison).

- [ ] 11. Automated tests
  - Add unit tests for `_zip_state_result` and `_federal_district_result` using
    small in-memory fixtures (match, mismatch, blank, not-found cases).
  - _Note: add only if the team wants regression coverage._

- [ ] 12. Confirm handling of "ZIP NOT FOUND"
  - Decide whether ZIPs missing from the lookup should stay flagged as-is,
    be treated as passing, or use a different message. Current behavior:
    flagged as `ZIP STATE PROBLEM. ZIP NOT FOUND`.
