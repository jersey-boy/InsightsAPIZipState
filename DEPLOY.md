# Deploying as an AWS Lambda

This function pulls every signup from the **NationBuilder V2 API** (OAuth 2.0),
filters, runs the two validation checks (ZIP vs. state, federal district vs.
state), writes the CSV/XLSX reports to `/tmp`, uploads them to S3 (date-stamped),
and emails the problem reports as attachments.

Packaging options:

- **Primary: zip + AWS-managed pandas layer** (no Docker). Covered below.
- **Fallback: container image** (see the [appendix](#appendix-container-image)).

Entry point: `validate_zip_fed.handler`.

The reference values below match the live deployment (account `068238656047`,
region `eu-west-2`); substitute your own as needed.

---

## 1. Prerequisites

- AWS CLI configured with credentials that can create the function, role, and
  policies (`aws sts get-caller-identity` should work).
- Python 3.12 + pip locally (to build the deps zip).
- An S3 bucket for outputs (and, recommended, the reference workbook + recipient
  list).
- NationBuilder V2 OAuth credentials: client id, client secret, and a refresh
  token obtained from the one-time authorize handshake (see
  [`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md)).

---

## 2. Upload the reference workbook to S3

The ~4 MB `ZIP_Locale_Detail.xlsx` is not bundled; the function fetches it from
S3 at runtime via `ZIP_LOOKUP_S3_URI`.

```bash
aws s3 cp ZIP_Locale_Detail.xlsx s3://YOUR_BUCKET/refs/ZIP_Locale_Detail.xlsx
```

---

## 3. Store NationBuilder V2 credentials in Secrets Manager

Put the V2 OAuth credentials in a JSON secret. The keys must match the
`NATIONBUILDER_*` names the code reads. The function **reads** these at startup
and **writes back** the rotated refresh token after each run (so write access is
required — see the IAM policy).

```bash
aws secretsmanager create-secret \
  --name nb/v2/prod \
  --region eu-west-2 \
  --secret-string '{
    "NATIONBUILDER_SLUG":"your-nation-slug",
    "NATIONBUILDER_CLIENT_ID":"your-oauth-client-id",
    "NATIONBUILDER_CLIENT_SECRET":"your-oauth-client-secret",
    "NATIONBUILDER_REFRESH_TOKEN":"your-refresh-token",
    "NATIONBUILDER_REDIRECT_URI":"https://localhost:8080/callback"
  }'
```

> **Why write access matters:** V2 access tokens expire after 24h, so the client
> refreshes on each run. NationBuilder rotates the refresh token on refresh, and
> the function persists the new one back into this secret. Without that, a
> scheduled run would eventually fail with an expired/stale token. (Store the
> secret as plain JSON with no UTF-8 BOM.)

---

## 4. Create the IAM execution role

The function needs to: write CloudWatch logs, read **and write** the secret
(for refresh-token rotation), read/write the S3 outputs, and send email.

`trust-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "lambda.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

`permissions-policy.json` (see `aws/permissions-policy.json`; scope ARNs to your
resources):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "Logs", "Effect": "Allow",
      "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],
      "Resource": "arn:aws:logs:eu-west-2:ACCOUNT_ID:*" },
    { "Sid": "ReadWriteV2Secret", "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue","secretsmanager:PutSecretValue"],
      "Resource": "arn:aws:secretsmanager:eu-west-2:ACCOUNT_ID:secret:nb/v2/prod-*" },
    { "Sid": "S3ReadWrite", "Effect": "Allow",
      "Action": ["s3:GetObject","s3:PutObject"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET/*" },
    { "Sid": "SendEmail", "Effect": "Allow",
      "Action": ["ses:SendEmail","ses:SendRawEmail"],
      "Resource": "*",
      "Condition": { "StringEquals": { "ses:FromAddress": "reports@YOUR_DOMAIN" } } }
  ]
}
```

```bash
aws iam create-role --role-name zipstatefed-lambda-role \
  --assume-role-policy-document file://aws/trust-policy.json

aws iam put-role-policy --role-name zipstatefed-lambda-role \
  --policy-name zipstatefed-permissions \
  --policy-document file://aws/permissions-policy.json
```

---

## 5. Build the deployment zip

`build_lambda_zip.py` installs the non-pandas dependencies as **Linux wheels** and
zips them with `validate_zip_fed.py` and the `nationbuilder_v2` package.
pandas/numpy are excluded — they come from the AWS-managed pandas layer.

```bash
python build_lambda_zip.py        # -> lambda_deploy.zip (~1 MB)
```

---

## 6. Find the managed pandas layer ARN

AWS SDK for pandas ships pandas + numpy as a managed layer (AWS account
`336392948345`). For eu-west-2 / Python 3.12 / x86_64:

```
arn:aws:lambda:eu-west-2:336392948345:layer:AWSSDKPandas-Python312:<version>
```

Pick the current `<version>` from the Lambda console (Layers → AWS layers) or the
[AWS SDK for pandas layers docs](https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html).

---

## 7. Create the function

```bash
aws lambda create-function \
  --function-name zipstatefed-validation \
  --runtime python3.12 \
  --role arn:aws:iam::ACCOUNT_ID:role/zipstatefed-lambda-role \
  --handler validate_zip_fed.handler \
  --zip-file fileb://lambda_deploy.zip \
  --layers arn:aws:lambda:eu-west-2:336392948345:layer:AWSSDKPandas-Python312:31 \
  --timeout 900 \
  --memory-size 3008 \
  --region eu-west-2 \
  --environment file://aws/lambda-env.json
```

`aws/lambda-env.json` holds the non-secret config:

```json
{
  "Variables": {
    "OUTPUT_DIR": "/tmp",
    "S3_BUCKET": "YOUR_BUCKET",
    "S3_PREFIX": "zip-state-reports/",
    "S3_REGION": "eu-west-2",
    "ZIP_LOOKUP_S3_URI": "s3://YOUR_BUCKET/refs/ZIP_Locale_Detail.xlsx",
    "SES_SENDER": "reports@YOUR_DOMAIN",
    "SES_REGION": "eu-west-2",
    "RECIPIENTS_S3_URI": "s3://YOUR_BUCKET/config/recipients.txt",
    "NATIONBUILDER_SECRET_ID": "nb/v2/prod",
    "NATIONBUILDER_SECRET_REGION": "eu-west-2"
  }
}
```

Sizing notes:

- **Memory 3008 MB**: the full V2 pull holds ~188k signup records (with nested
  address/custom-value objects) plus a pandas DataFrame in memory. At 1024 MB the
  function is killed with `Runtime.OutOfMemory`; 3008 MB runs comfortably (and
  gives ~2 vCPU, so it is faster too).
- **Timeout 900 s** (the max): the parallel full pull is ~4–5 min; 900 s leaves
  headroom.
- **/tmp**: the output files (six) total well under the 512 MB `/tmp` default.

To ship code updates later:

```bash
python build_lambda_zip.py
aws lambda update-function-code \
  --function-name zipstatefed-validation \
  --zip-file fileb://lambda_deploy.zip --region eu-west-2
```

---

## 8. Test the function

```bash
aws lambda invoke --function-name zipstatefed-validation \
  --payload '{}' --cli-read-timeout 900 --region eu-west-2 response.json
cat response.json
```

A success looks like:

```json
{"statusCode": 200, "result": {"status": "OK", "kept": 179540,
 "zip_state_problems": 95, "federal_district_problems": 986,
 "emailed_to": ["someone@example.com"]}}
```

Errors return `statusCode: 500` with an `error` field; the full traceback is in
CloudWatch Logs.

---

## 9. Email the report attachments (Amazon SES)

The run emails the two problem reports as attachments when `SES_SENDER` is set and
a recipient list exists. One-time SES setup:

- **Verify a sender identity.** A verified domain (DKIM) gives the best
  deliverability; if the domain is in Route 53, SES can add the DKIM records.
  The sender (`SES_SENDER`) can be any address on the verified domain.
- **Sandbox vs. production.** New SES accounts are in the sandbox and can only
  send to *verified* recipients; verify each recipient, or request production
  access. Check with
  `aws sesv2 get-account --region eu-west-2 --query ProductionAccessEnabled`.
- **Recipient list** is a plain-text file in S3 (one address per line, `#`
  comments): `aws s3 cp recipients.txt s3://YOUR_BUCKET/config/recipients.txt`.
  Edit that object to change recipients — no redeploy needed.

The IAM policy already grants `ses:SendEmail`/`ses:SendRawEmail` scoped to the
from-address (`SendRawEmail` carries the attachments). `EMAIL_MESSAGE` overrides
the default body note (which explains the CSV-vs-XLSX leading-zero ZIP issue).

---

## 10. Schedule it (optional)

Run nightly with EventBridge Scheduler:

```bash
aws scheduler create-schedule \
  --name zipstatefed-nightly \
  --schedule-expression "cron(0 6 * * ? *)" \
  --schedule-expression-timezone "UTC" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target '{
    "Arn":"arn:aws:lambda:eu-west-2:ACCOUNT_ID:function:zipstatefed-validation",
    "RoleArn":"arn:aws:iam::ACCOUNT_ID:role/zipstatefed-scheduler-role"
  }' \
  --region eu-west-2
```

The scheduler role needs `lambda:InvokeFunction` on the function.

---

## Appendix: container image

If you prefer a container image, a `Dockerfile` and `.dockerignore` are included.
This path installs pandas into the image from `requirements.txt` (no managed
layer). Build, push to ECR, and create the function with `--package-type Image`.
Everything else (IAM role, the `nb/v2/prod` secret, env vars, scheduling) is the
same as the zip path above.

```bash
aws ecr create-repository --repository-name zipstatefed-validation --region eu-west-2
# build + push the image, then:
aws lambda create-function --function-name zipstatefed-validation \
  --package-type Image \
  --code ImageUri=ACCOUNT_ID.dkr.ecr.eu-west-2.amazonaws.com/zipstatefed-validation:latest \
  --role arn:aws:iam::ACCOUNT_ID:role/zipstatefed-lambda-role \
  --timeout 900 --memory-size 3008 --region eu-west-2 \
  --environment file://aws/lambda-env.json
```
