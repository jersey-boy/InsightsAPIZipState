# Tasks — Zip / State / Federal District Validation

Implementation plan. Checked items are already built and verified against the
live Insights site. Latest run: 177,923 records pulled; 90 ZIP/state problems,
976 federal district problems, reported in two separate files (see task 7).

ZIP → state validation now uses the USPS `ZIP_Locale_Detail.xlsx` workbook
(sheets `Detail` + `Unique` + `Other` merged), keyed by delivery ZIP by default
(`ZIP_MODE`). This superseded the original single-file `zip_state_lookup.csv`.

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
  - [x] 5.1 Build integer-keyed `zip -> state` lookup from `ZIP_Locale_Detail.xlsx`
        (merging the `Detail`, `Unique`, and `Other` sheets; `Detail` wins on
        conflicts). Keyed by delivery or physical ZIP via `ZIP_MODE`.
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

- [x] 7. Output files (CSV + Excel-friendly XLSX for each)
  - [x] 7.1 Write full annotated dataset with both flags
        (`zip_state_federal_district.csv` / `.xlsx`).
    - _Requirements: 6.1, 6.3_
  - [x] 7.2 Write two separate problems reports, one per test:
        `zip_state_problems.csv` / `.xlsx` (ZIP/state flag) and
        `federal_district_problems.csv` / `.xlsx` (federal district flag).
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

- [x] 10. Optional ZIP formatting
  - Done: output CSVs zero-pad `registered_zip_clean` to 5-digit text
    (e.g. `01002`), and the XLSX files type that column as text so Excel keeps
    the leading zeros.

- [ ] 11. Automated tests
  - Add unit tests for `_zip_state_result` and `_federal_district_result` using
    small in-memory fixtures (match, mismatch, blank, not-found cases).
  - _Note: add only if the team wants regression coverage._

- [x] 12. Confirm handling of "ZIP NOT FOUND"
  - Decided: keep the `ZIP STATE PROBLEM. ZIP NOT FOUND` message. Investigation
    showed the remaining not-found ZIPs are placeholder / unassigned values
    (e.g. `20000`), not non-US/APO, so a special "non-US" message would be
    misleading. Merging the `Unique`/`Other` sheets already resolved ~195 valid
    ZIPs that the `Detail` sheet alone was missing.
