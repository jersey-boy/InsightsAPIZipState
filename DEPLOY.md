# Deploying as an AWS Lambda

This function pulls the "Zip State Federal District" Insights view, runs the two
validation checks, writes the CSV/XLSX reports to `/tmp`, uploads them to S3, and
returns presigned download URLs.

There are two packaging options:

- **Primary: zip + AWS-managed pandas layer** (recommended, no Docker). Covered
  below.
- **Fallback: container image** (see the [appendix](#appendix-container-image)).

The entry point is `pull_zip_state_federal.handler`.

---

## 1. Prerequisites

- AWS CLI configured with credentials that can create the function, role, and
  policies (`aws sts get-caller-identity` should work).
- Python 3.12 + pip locally (to build the deps zip).
- An S3 bucket for outputs (and, recommended, for the reference workbook).
- The NationBuilder Insights personal access token details.

Pick a region and reuse it everywhere below (examples use `us-east-1`).

---

## 2. Upload the reference workbook to S3

The ~4 MB `ZIP_Locale_Detail.xlsx` is not bundled; the function fetches it from
S3 at runtime via `ZIP_LOOKUP_S3_URI`.

```bash
aws s3 cp ZIP_Locale_Detail.xlsx s3://YOUR_BUCKET/refs/ZIP_Locale_Detail.xlsx
```

---

## 3. Store credentials in Secrets Manager

Put the NationBuilder credentials in a JSON secret. The keys must match the
`NB_INSIGHTS_*` names the code reads.

```bash
aws secretsmanager create-secret \
  --name nb/insights/prod \
  --secret-string '{
    "NB_INSIGHTS_SERVER":"https://login.insights.nationbuilder.com",
    "NB_INSIGHTS_API_VERSION":"3.23",
    "NB_INSIGHTS_SITE_NAME":"your-site-name",
    "NB_INSIGHTS_SITE_URL":"your-site-url",
    "NB_INSIGHTS_TOKEN_NAME":"your-token-name",
    "NB_INSIGHTS_TOKEN_SECRET":"your-token-secret"
  }'
```

Note the returned secret ARN.

---

## 4. Create the IAM execution role

The function needs to: write CloudWatch logs, read the secret, and read/write
the S3 outputs (write objects, and read them back to presign).

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

`permissions-policy.json` (scope the ARNs to your bucket/secret):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Logs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Sid": "ReadSecret",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:nb/insights/prod-*"
    },
    {
      "Sid": "S3ReadWrite",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET/*"
    }
  ]
}
```

Presigned GET URLs are signed locally from the role's credentials and need no
extra permission beyond `s3:GetObject`.

```bash
aws iam create-role --role-name zipstatefed-lambda-role \
  --assume-role-policy-document file://trust-policy.json

aws iam put-role-policy --role-name zipstatefed-lambda-role \
  --policy-name zipstatefed-permissions \
  --policy-document file://permissions-policy.json
```

---

## 5. Build the deployment zip

`build_lambda_zip.py` installs the non-pandas dependencies as **Linux wheels**
(so they run on Lambda even when built on Windows) and zips them with the two
source files. pandas/numpy are excluded — they come from the managed layer.

```bash
python build_lambda_zip.py
# -> lambda_deploy.zip  (~3 MB)
```

---

## 6. Find the managed pandas layer ARN

AWS SDK for pandas (formerly awswrangler) ships pandas + numpy as a managed
layer, published by AWS account `336392948345` in every commercial region:

```
arn:aws:lambda:<region>:336392948345:layer:AWSSDKPandas-Python312:<version>
```

Pick the latest `<version>` for your region from the AWS console (Lambda ->
Layers -> "Add a layer" -> AWS layers), or the
[AWS SDK for pandas install docs](https://aws-sdk-pandas.readthedocs.io/en/stable/install.html).
Example used below: `...:AWSSDKPandas-Python312:13` (replace with the current one).

---

## 7. Create the function

```bash
aws lambda create-function \
  --function-name zipstatefed-validation \
  --runtime python3.12 \
  --role arn:aws:iam::ACCOUNT_ID:role/zipstatefed-lambda-role \
  --handler pull_zip_state_federal.handler \
  --zip-file fileb://lambda_deploy.zip \
  --layers arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:13 \
  --timeout 300 \
  --memory-size 1024 \
  --environment "Variables={
    OUTPUT_DIR=/tmp,
    S3_BUCKET=YOUR_BUCKET,
    S3_PREFIX=zip-state-reports/,
    S3_REGION=us-east-1,
    ZIP_LOOKUP_S3_URI=s3://YOUR_BUCKET/refs/ZIP_Locale_Detail.xlsx,
    NB_INSIGHTS_SECRET_ID=nb/insights/prod,
    NB_INSIGHTS_SECRET_REGION=us-east-1
  }"
```

Sizing notes:

- **Memory 1024 MB+**: the AWS SDK for pandas docs warn that <512 MB can be
  insufficient for pandas workloads; 178K rows + XLSX writing wants headroom.
  More memory also means more CPU, so it finishes faster.
- **Timeout 300 s**: the Tableau pull plus XLSX generation takes a while.
- **/tmp**: the six output files total well under the 512 MB `/tmp` default.

To ship code updates later:

```bash
python build_lambda_zip.py
aws lambda update-function-code \
  --function-name zipstatefed-validation \
  --zip-file fileb://lambda_deploy.zip
```

---

## 8. Test the function

```bash
aws lambda invoke \
  --function-name zipstatefed-validation \
  --payload '{}' response.json
cat response.json
```

A success looks like:

```json
{"statusCode": 200, "result": {"status": "OK", "rows": 178073,
 "zip_state_problems": 91, "federal_district_problems": 989,
 "presigned_urls": ["https://YOUR_BUCKET.s3..."]}}
```

The presigned URLs are the shareable download links (valid up to 7 days; tune
with `PRESIGN_EXPIRY_SECONDS`). Errors are returned with `statusCode: 500` and an
`error` field, and the full traceback is in CloudWatch Logs.

---

## 9. Schedule it (optional)

Run nightly with EventBridge Scheduler:

```bash
aws scheduler create-schedule \
  --name zipstatefed-nightly \
  --schedule-expression "cron(0 6 * * ? *)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target '{
    "Arn":"arn:aws:lambda:us-east-1:ACCOUNT_ID:function:zipstatefed-validation",
    "RoleArn":"arn:aws:iam::ACCOUNT_ID:role/zipstatefed-scheduler-role"
  }'
```

The scheduler role needs `lambda:InvokeFunction` on the function. (Alternatively
use an EventBridge rule with `aws events put-rule` + `aws lambda add-permission`.)

---

## Appendix: container image

If you prefer a container image (e.g. dependencies grow, or you want exact
runtime parity), a `Dockerfile` and `.dockerignore` are included. This path does
**not** use the pandas layer — pandas is installed into the image from
`requirements.txt`.

```bash
# Build (requires the Docker engine running locally) and push to ECR:
aws ecr create-repository --repository-name zipstatefed-validation
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

docker build -t zipstatefed-validation .
docker tag zipstatefed-validation:latest \
  ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/zipstatefed-validation:latest
docker push ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/zipstatefed-validation:latest

# Create the function from the image (no --layers, no --runtime/--handler):
aws lambda create-function \
  --function-name zipstatefed-validation \
  --package-type Image \
  --code ImageUri=ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/zipstatefed-validation:latest \
  --role arn:aws:iam::ACCOUNT_ID:role/zipstatefed-lambda-role \
  --timeout 300 --memory-size 1024 \
  --environment "Variables={OUTPUT_DIR=/tmp, S3_BUCKET=YOUR_BUCKET, ...}"
```

Everything else (IAM role, secret, `ZIP_LOOKUP_S3_URI`, env vars, scheduling) is
identical to the zip path above.
