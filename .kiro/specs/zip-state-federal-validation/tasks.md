# Tasks — Zip / State / Federal District Validation (NationBuilder V2)

Implementation record. The system is built, deployed, and verified against the
live NationBuilder V2 API and AWS account `068238656047` (eu-west-2).

Latest live run: ~188.5k signups pulled, **179,540 kept** after the filter,
**95 ZIP/state** problems, **986 federal-district** problems; dated reports in S3
and emailed.

> History note: the project originally sourced data from NationBuilder Insights
> (Tableau) and later added a NationBuilder V1 cross-check. It was then migrated
> to be **V2-only**, and all Insights/Tableau and V1 code was removed. The tasks
> below describe the current V2 system.

---

## 1. NationBuilder V2 client (`nationbuilder_v2/`)

- [x] 1.1 OAuth 2.0 refresh-token client (`client.py`): `from_env()` mints an
  access token from the refresh token; `refresh_access_token()` rotates it and
  invokes an `on_refresh` callback to persist the new token.
  - _Requirements: 1_
- [x] 1.2 Parallel full-nation pull (`fetch_all_signups`): page-number
  pagination fetched via a thread pool (default 20), batched, stopping on an
  empty batch; `_get_page` retries on 429/5xx. ~4–5 min for ~188k.
  - _Why:_ serial paging (~19 min) exceeds the Lambda max. _Requirements: 3_

## 2. Extract + filter (`nationbuilder_v2/extract.py`)

- [x] 2.1 `project_signup`: signup_id, registered_state, registered_zip (5-digit),
  federal_district, us_citizen, date_last_verified. us_citizen/date_last_verified
  are custom fields (in `custom_values`), so the pull requests
  `extra_fields[signups]=registered_address,custom_values`.
  - _Requirements: 2_
- [x] 2.2 Inclusion filter (`keep_row`): non-blank zip AND truthy us_citizen AND
  non-blank date_last_verified; `extract_filtered_signups` returns kept rows +
  exclusion stats.
  - _Requirements: 4_

## 3. Validation pipeline (`validate_zip_fed.py`)

- [x] 3.1 ZIP/state check against the merged ZIP reference (integer match;
  SHOULD BE / ZIP IS BLANK / ZIP NOT FOUND).
  - _Requirements: 5, 8_
- [x] 3.2 Federal-district check: two-letter prefix of `federal_district` vs.
  `registered_state` (regex-derived prefix; blank → explicit message).
  - _Requirements: 6_
- [x] 3.3 Write full dataset + two per-test problem reports (CSV + XLSX; ZIP typed
  as text in XLSX).
  - _Requirements: 7_
- [x] 3.4 Status file + result dict (counts, filter stats, files, URLs, emailed);
  `main()` + `handler()`.
  - _Requirements: 12_

## 4. ZIP reference

- [x] 4.1 Merge `Detail` + `Unique` + `Other` sheets of `ZIP_Locale_Detail.xlsx`
  (Detail wins; stacked-header handling), delivery ZIP by default (`ZIP_MODE`).
  Sourced locally or from S3 (`ZIP_LOOKUP_S3_URI`). See
  `ZIP_REFERENCE_AND_ERRORS.md`.
  - _Requirements: 8_

## 5. S3 output + email

- [x] 5.1 Date-stamped S3 upload (`_YYYYMMDD`) + presigned URLs; no-op when
  `S3_BUCKET` unset. _Requirements: 9_
- [x] 5.2 SES email of the two problem reports as attachments (full dataset
  excluded; size guard; configurable `EMAIL_MESSAGE`); recipients from S3/local
  list. _Requirements: 11_

## 6. AWS deployment

- [x] 6.1 Secrets Manager secret `nb/v2/prod` with the V2 OAuth creds; code loads
  it via `NATIONBUILDER_SECRET_ID` and writes the rotated refresh token back.
  - _Requirements: 1.2, 10.2_
- [x] 6.2 IAM role `zipstatefed-lambda-role`: logs, Get+Put on `nb/v2/prod`, S3
  Get/Put, SES send scoped to the from-address.
- [x] 6.3 `build_lambda_zip.py` packages `validate_zip_fed.py` + `nationbuilder_v2`
  (pandas/numpy excluded — managed layer); ~1 MB zip.
- [x] 6.4 Function `zipstatefed-validation`: handler `validate_zip_fed.handler`,
  layer `AWSSDKPandas-Python312`, **3008 MB** (raised after a 1024 MB
  `Runtime.OutOfMemory`), 900 s timeout.
  - _Requirements: 3.3, 10_
- [x] 6.5 EventBridge Scheduler `zipstatefed-nightly` (cron `0 6 * * ? *` UTC).
- [x] 6.6 Verified live: 200 OK, 179,540 kept, dated S3 objects present, emailed,
  refresh token rotation persisted to the secret.

## 7. Legacy removal

- [x] 7.1 Deleted the Insights/Tableau code (`pull_zip_state_federal.py`,
  `nb_insights.py`, `list_reports.py`, `sources.py`) and the V1 `nationbuilder/`
  package; dropped `tableau-api-lib`.
- [x] 7.2 Updated build/packaging, `.env.example`, and all docs to V2-only.

---

## Open / follow-up tasks

- [ ] 8. SES production access — required before adding recipients that can't be
  individually verified (sandbox only sends to verified addresses).
- [ ] 9. Rotate the NationBuilder OAuth client secret periodically (it lives in
  `.env` and Secrets Manager).
- [ ] 10. Optional: automated unit tests for the checks and the extract/filter.
- [ ] 11. Optional: infrastructure-as-code (SAM/CDK/Terraform) for repeatable
  deploys instead of the current CLI steps.
