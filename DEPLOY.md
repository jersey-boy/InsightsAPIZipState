# Deploying the validation Lambda (Serverless Framework v3)

The function pulls every signup from the **NationBuilder V2 API** (OAuth 2.0),
filters, runs the two validation checks (ZIP vs. state, federal district vs.
state), writes the CSV/XLSX reports to `/tmp`, uploads them to S3 (date-stamped),
and emails the problem reports as attachments.

Deployment is **infrastructure-as-code via Serverless Framework v3**.
CloudFormation (through Serverless) owns the Lambda, its IAM execution role, the
log group, and the nightly EventBridge schedule. The **S3 reports bucket** and the
**Secrets Manager secret** are *not* managed by the stack — they hold live state
(report history + the rotating V2 refresh token), so they are created by hand and
only **referenced** by `serverless.yml`. A `serverless remove` must never wipe
report history or the live token.

Live deployment: AWS account `068238656047`, region `eu-west-2`, stack
`zipstatefed-prod`, function `zipstatefed-validation`.

---

## How it deploys

- **Automatically** on every push to `main` via GitHub Actions
  (`.github/workflows/deploy.yml`) — the normal path.
- **Manually** from the Actions tab (`workflow_dispatch`), or locally with the
  AWS CLI configured.

Both paths run the same two steps: build the Lambda artifact, then
`serverless deploy`.

```bash
# Local deploy (needs the AWS CLI configured + Node 20 + Python 3.12):
npm install                       # once, installs Serverless v3 locally
python build_lambda_zip.py        # produces lambda_deploy.zip
npx serverless deploy --stage prod --region eu-west-2
```

`build_lambda_zip.py` installs the non-pandas deps as **Linux manylinux wheels**
and zips them with `validate_zip_fed.py` + the `nationbuilder_v2` package;
pandas/numpy come from the AWS-managed pandas layer referenced in
`serverless.yml`. The artifact is shipped as-is via `package.artifact`.

---

## GitHub Actions (CI/CD)

`.github/workflows/deploy.yml` deploys on push to `main` (docs-only changes are
skipped via `paths-ignore`), with a `concurrency` group so two deploys can't race
the stack, and a `workflow_dispatch` trigger for manual runs.

**Required repository secrets** (Settings → Secrets and variables → Actions):

| Secret | What it is |
|--------|-----------|
| `AWS_ACCESS_KEY_ID` | IAM user key allowed to deploy the stack |
| `AWS_SECRET_ACCESS_KEY` | its secret |
| `SERVERLESS_ACCESS_KEY` | Serverless dashboard access key (v3 login) |

> **Gotcha:** credential errors in CI are almost always **whitespace** in a
> pasted secret (a trailing newline in `AWS_SECRET_ACCESS_KEY` →
> `Invalid character in header content ["Authorization"]`). Re-paste cleanly.

> **Hardening (future):** static IAM keys are the simple start. Prefer GitHub
> **OIDC** (a GitHub→AWS trust role, no stored long-lived keys) and a dedicated
> least-privilege deploy user rather than personal admin keys.

---

## One-time prerequisites (not managed by the stack)

These already exist for the live deployment; recreate them only for a fresh
environment.

### 1. S3 bucket (referenced, not managed)

`zipstatefed-reports-068238656047`, with Block Public Access on. Holds the
reference workbook (`refs/`), the dated reports (`zip-state-reports/`), and the
recipient list (`config/recipients.txt`).

```bash
aws s3 cp ZIP_Locale_Detail.xlsx s3://YOUR_BUCKET/refs/ZIP_Locale_Detail.xlsx
aws s3 cp recipients.txt          s3://YOUR_BUCKET/config/recipients.txt
```

### 2. Secrets Manager secret (referenced, not managed)

`nb/v2/prod` — a plain-JSON secret (no BOM) with the V2 OAuth credentials. The
function **reads** these and **writes back** the rotated refresh token each run,
so the IAM role (defined in `serverless.yml`) grants both `GetSecretValue` and
`PutSecretValue`.

```bash
aws secretsmanager create-secret --name nb/v2/prod --region eu-west-2 \
  --secret-string '{
    "NATIONBUILDER_SLUG":"your-nation-slug",
    "NATIONBUILDER_CLIENT_ID":"your-oauth-client-id",
    "NATIONBUILDER_CLIENT_SECRET":"your-oauth-client-secret",
    "NATIONBUILDER_REFRESH_TOKEN":"your-refresh-token",
    "NATIONBUILDER_REDIRECT_URI":"https://localhost:8080/callback"
  }'
```

> V2 access tokens expire after 24h, so the client refreshes each run and
> NationBuilder rotates the refresh token; the function persists the new one back
> into this secret. Without write access a scheduled run would eventually fail.
> See `V2_OAUTH_SETUP.md`.

### 3. SES sender + recipients

- Verify a sending identity (a Route 53 domain with DKIM is best). The sender
  (`SES_SENDER` in `serverless.yml`) can be any address on the verified domain.
- SES **sandbox** only sends to verified recipients — the #1 "email didn't send"
  cause. Check `aws sesv2 get-account --region eu-west-2 --query ProductionAccessEnabled`.
- Edit `s3://YOUR_BUCKET/config/recipients.txt` to change recipients — no
  redeploy needed.

---

## `serverless.yml` at a glance

- **Owns:** the Lambda (`zipstatefed-validation`, handler
  `validate_zip_fed.handler`, 3008 MB, 900 s), its IAM role, log group, and the
  nightly `cron(0 6 * * ? *)` schedule.
- **References:** bucket, secret, SES sender, and the pandas layer (all under
  `custom:`). The AWS **account id is hardcoded** in `custom:` on purpose — the
  `${aws:accountId}` variable triggers an STS call during config resolution that
  can fail cryptically before deploy starts; the account id isn't secret.
- **Sizing:** 3008 MB because the full V2 pull holds ~188k records plus a pandas
  DataFrame — 1024 MB gets OOM-killed. 900 s is the Lambda max; the parallel pull
  runs well inside it.

---

## Migrating from the old hand-built deployment

The function was originally created by hand with the AWS CLI. To let Serverless
own it, the hand-made resources must be removed first — in particular the **log
group**, which otherwise blocks the stack create under the
`AWS::EarlyValidation::ResourceExistenceCheck` guardrail:

```bash
aws scheduler delete-schedule --name zipstatefed-nightly --region eu-west-2
aws lambda delete-function --function-name zipstatefed-validation --region eu-west-2
aws logs delete-log-group --log-group-name /aws/lambda/zipstatefed-validation --region eu-west-2
```

Then `serverless deploy`. (The hand-made IAM roles `zipstatefed-lambda-role` and
the old scheduler role become unused — Serverless creates its own role
`zipstatefed-prod-<region>-lambdaRole`. They can be deleted once you've confirmed
the stack works.) This migration has already been done for the live deployment.

---

## Test the function

```bash
aws lambda invoke --function-name zipstatefed-validation \
  --payload '{}' --cli-read-timeout 900 --region eu-west-2 response.json
cat response.json
```

A success looks like:

```json
{"statusCode": 200, "result": {"status": "OK", "kept": 180364,
 "zip_state_problems": 98, "federal_district_problems": 1048,
 "emailed_to": ["someone@example.com"]}}
```

Errors return `statusCode: 500` with an `error` field; the full traceback is in
CloudWatch Logs (`/aws/lambda/zipstatefed-validation`).

---

## Removing the stack

`npm run remove` (or `npx serverless remove`) deletes the function, role, log
group, and schedule — but **not** the S3 bucket or the secret, which are not
managed by the stack. That is deliberate: report history and the live refresh
token survive a stack teardown.
