# Tasks — Zip / State / Federal District Validation

Implementation history, grouped into phases. Each task notes **what** was done,
**why**, and **how**, plus how it was verified. Checked items are built and
verified against the live Insights site and (for the AWS phases) the live AWS
account `068238656047` in `eu-west-2`.

Current run figures (delivery mode, three-sheet reference): ~178,073 records;
~91 ZIP/state problems, ~989 federal district problems, reported in two separate
files. (Counts drift slightly run-to-run as the live data changes.)

---

## Phase 1 — Local validation tool

- [x] 1. Project scaffolding and secrets handling
  - `.env`, `.env.example`, `.gitignore`, `requirements.txt`; `.env` and output
    files gitignored.
  - _Why:_ keep the token out of source; make first-run setup obvious.
  - _Requirements: 1.1_

- [x] 2. Shared connection module (`nb_insights.py`)
  - [x] 2.1 Load `.env` and build the `tableau_api_lib` config from env vars.
  - [x] 2.2 `_require()` raises `MissingCredentialsError` on missing vars.
  - [x] 2.3 `insights_connection()` context manager (sign in / always sign out).
  - _Why:_ one reusable, safe connection path; guaranteed sign-out avoids stale
    sessions. _Requirements: 1.1–1.3_

- [x] 3. Content inventory app (`list_reports.py`)
  - Lists workbooks/views/data sources to CSV; used to find the target view id
    (`c7f541b2-87db-4fad-97c5-2e532dbbe956`). _Requirements: supporting_

- [x] 4. Pull + shape the view data (`pull_zip_state_federal.py`)
  - [x] 4.1 Fetch by view id.
  - [x] 4.2 Reorder columns; append unexpected columns (no data loss).
  - [x] 4.3 Add blank `zip_state_problem` / `federal_district_problem`.
  - _Requirements: 2, 3_

- [x] 5. ZIP / state validation
  - [x] 5.1 Build integer-keyed `zip -> state` lookup (initially from a single
    `zip_state_lookup.csv`; later replaced — see Phase 2).
  - [x] 5.2 Precedence: blank ZIP → not found → match (blank) → `SHOULD BE`.
  - _Why integer keys:_ normalizes leading-zero ZIPs without string padding.
  - _Requirements: 4_

- [x] 6. Federal district validation
  - [x] 6.1 Compare `fed_dist_prefix` to `registered_state`.
  - [x] 6.2 Blank prefix → `FEDERAL DISTRICT IS BLANK`.
  - [x] 6.3 Drop `fed_dist_prefix` before output.
  - _Why:_ the state is already in the district code, so no reference needed.
  - _Requirements: 5_

- [x] 7. Output + run visibility
  - [x] 7.1 Full annotated CSV/XLSX; ZIP zero-padded (CSV) and typed as text
    (XLSX) to preserve leading zeros.
  - [x] 7.2 Status file with counts + problem breakdown; `STATUS: OK/ERROR`.
  - _Why the status file:_ the dev drive's shell mangled output; a written record
    is the reliable log. _Requirements: 6, 6a, 7_

---

## Phase 2 — Reference migration & accuracy

- [x] 8. Switch to `ZIP_Locale_Detail.xlsx`
  - [x] 8.1 Read the USPS workbook; add `ZIP_MODE` (delivery/physical).
  - [x] 8.2 Investigate physical vs delivery: physical produced ~33K false
    "ZIP NOT FOUND" (facility-ZIP coverage gap); switched default to delivery.
  - [x] 8.3 Merge `Detail` + `Unique` + `Other` (Detail wins), handling the
    3-row stacked header on the latter two. Recovered ~195 valid ZIPs.
  - [x] 8.4 Decision: keep `ZIP NOT FOUND` (remaining ones are placeholder/
    unassigned ZIPs, **not** non-US/APO — so no "non-US" message added).
  - _How verified:_ compared member ZIPs against each sheet's coverage; not-found
    dropped from ~33,578 → ~90. _Requirements: 8_

- [x] 9. Documentation: `ZIP_REFERENCE_AND_ERRORS.md`
  - Explains the three-sheet layout/merge, `ZIP_MODE`, and every error message.

- [x] 10. Split into two per-test reports
  - `zip_state_problems.*` and `federal_district_problems.*`, each carrying only
    its own flag; full dataset retained with both flags.
  - _Why:_ the checks are reviewed independently. _Requirements: 6_

- [x] 11. Remove PII (`full_name`) from all outputs
  - Dropped right after fetch; `signup_id` kept for traceability.
  - _Requirements: 2.2_

- [x] 12. Git hygiene
  - Restored `.env.example` after it was accidentally overwritten with real
    credentials (prevented a secret leak); untracked + gitignored the status
    file (contains member names).

---

## Phase 3 — S3 output + presigned URLs

- [x] 13. Configurable output location (`OUTPUT_DIR`, default `.`)
  - All outputs + status file routed through `_out()`. _Requirements: 9.3, 10.2_

- [x] 14. Optional S3 upload + presigned URLs
  - `upload_to_s3()` gated on `S3_BUCKET`; presigned GET per file
    (`PRESIGN_EXPIRY_SECONDS`, default 7 days). boto3 lazy-imported; credentials
    from AWS profile locally / role in Lambda.
  - _How verified:_ mocked boto3 (keys, one upload per file, one URL per file).
  - _Requirements: 9_

---

## Phase 4 — Lambda-ready refactor

- [x] 15. Handler + `run()` split
  - Refactored `main()` into `run()` (returns result dict); added `main()` and
    `handler(event, context)` → `{statusCode, result}`. _Requirements: 10.1, 7.3_

- [x] 16. Secrets Manager loading (`nb_insights.py`)
  - `_load_secret_into_env()` fills only-missing vars from `NB_INSIGHTS_SECRET_ID`
    (local `.env` wins). Hardened later for the BOM bug (Phase 5). _Req: 10.3_

- [x] 17. S3-sourced reference (`ZIP_LOOKUP_S3_URI`)
  - `resolve_reference_file()` downloads to `OUTPUT_DIR` when set, else local.
  - _Why:_ keeps the ~4 MB workbook out of the package; updatable without
    redeploy. _Requirements: 10.2_

- [x] 18. Packaging decision + build
  - Chose zip + AWS-managed pandas layer over Docker (pandas Windows-wheel
    problem; no Docker engine locally). `build_lambda_zip.py` installs Linux
    wheels and prunes pandas/numpy → ~2.8 MB zip. `Dockerfile`/`.dockerignore`
    kept as fallback. _How verified:_ inspected zip contents (source + deps, no
    pandas/numpy). _Requirements: 10.4_

- [x] 19. `DEPLOY.md`
  - zip + layer as primary path; container image as appendix; later extended
    with the SES section.

---

## Phase 5 — AWS deployment (eu-west-2, account 068238656047)

- [x] 20. S3 bucket `zipstatefed-reports-068238656047`
  - All four Block Public Access settings enabled (PII). _Requirements: 9.4_
- [x] 21. Upload reference workbook to `refs/`.
- [x] 22. Secrets Manager secret `nb/insights/prod` (built from local `.env`).
- [x] 23. IAM execution role `zipstatefed-lambda-role`
  - Least-privilege: scoped logs, `secretsmanager:GetSecretValue`,
    `s3:GetObject/PutObject`, (later) scoped `ses:SendEmail`.
- [x] 24. Pandas layer resolved:
  `arn:aws:lambda:eu-west-2:336392948345:layer:AWSSDKPandas-Python312:31`.
- [x] 25. Create function `zipstatefed-validation` (python3.12, 1024 MB, 300 s).
- [x] 26. Fix the Secrets Manager **BOM bug**
  - First invoke failed with `JSONDecodeError` at char 0: PowerShell
    `Set-Content -Encoding utf8` wrote a UTF-8 BOM into the secret. Re-stored
    without a BOM (.NET `UTF8Encoding($false)`) AND hardened
    `_load_secret_into_env` (strip BOM, handle `SecretBinary`, clear errors).
  - _How verified:_ re-invoke returned 200 OK; six files confirmed in S3.
- [x] 27. Nightly schedule
  - EventBridge Scheduler `zipstatefed-nightly`, `cron(0 6 * * ? *)` UTC, via
    `zipstatefed-scheduler-role` (invoke-only).

---

## Phase 6 — Email notifications (Amazon SES)

- [x] 28. Verify SES sender
  - Domain `lategothikdata.com` verified via DKIM CNAMEs added to its Route 53
    zone; sender `insights-reports@lategothikdata.com`. Recipient
    `rick_meier@msn.com` also verified (SES sandbox).
- [x] 29. Recipient list in S3 (`config/recipients.txt`)
  - `load_recipients()` reads one-per-line, `#` comments, from S3 or local.
  - _Why S3:_ manage recipients without a redeploy. _Requirements: 11.5_
- [x] 30. `notify_by_email()`
  - Sends counts + presigned links via SES; body carries links only, never data.
  - _Requirements: 11.1, 11.4_
- [x] 31. Refinements
  - [x] 31.1 Email the **problem reports only**; full dataset uploaded to S3 but
    not linked (`EMAIL_EXCLUDE_FILES`). _Requirements: 11.3_
  - [x] 31.2 Configurable `EMAIL_MESSAGE`; default explains the CSV-vs-XLSX
    leading-zero ZIP issue. _Requirements: 11.2_
  - _How verified:_ mock (message present, full dataset excluded, 4 problem links)
    and a live invoke (200 OK, `emailed_to: rick_meier@msn.com`).

---

## Phase 7 — Attachments + dated S3 history

- [x] 37. Date-stamp S3 objects (`YYYYMMDD`, UTC)
  - `RUN_DATE` + `_dated_name()`; `upload_to_s3` writes dated object names so a
    history accumulates. Local file names stay undated.
  - _Why:_ keep every run's reports, not just the latest. _Requirements: 9.2_

- [x] 38. Email problem reports as attachments (not links)
  - Rewrote `notify_by_email` to build a `MIMEMultipart` and send via
    `ses.send_raw_email`, attaching the two problem reports (dated names);
    excludes the full dataset; skips any attachment over `SES_MAX_ATTACH_BYTES`
    with a note in the body. Email decoupled from S3 (attaches local files).
  - Added `ses:SendRawEmail` to the IAM policy.
  - Kept `_build_link_email()` as the documented, unused link alternative.
  - _How verified:_ mock (4 dated attachments, full dataset excluded, message
    present, link alt callable) and a live invoke (200 OK, emailed_to set, dated
    objects present in S3). _Requirements: 11.1–11.5_

---

## Open / follow-up tasks

- [ ] 32. SES production access
  - Request from the SES console before adding recipients that can't be
    individually verified (sandbox only sends to verified addresses).

- [ ] 33. Rotate the NationBuilder token
  - It currently lives in the local `.env` and in Secrets Manager. If rotated,
    update the secret with `put-secret-value` using the no-BOM approach.

- [ ] 34. Deliver-ability hardening (optional)
  - First emails from the new domain may land in spam until reputation builds;
    consider SPF/DMARC records if wider distribution is planned.

- [ ] 35. Automated unit tests (optional)
  - Fixtures for `_zip_state_result` / `_federal_district_result` (match,
    mismatch, blank, not-found) and for the merge/BOM/email helpers. Add only if
    the team wants regression coverage.

- [ ] 36. Infrastructure-as-code (optional)
  - The deploy is currently CLI-driven with policy JSON in `aws/`. If repeatable
    environments are needed, port to SAM/CDK/Terraform.
