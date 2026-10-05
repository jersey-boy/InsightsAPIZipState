"""
ZIP / state and federal-district validation, sourced from the NationBuilder V2 API.

This is the V2 pipeline that replaces the old Insights/Tableau flow: it pulls all
signups from NationBuilder V2 (parallel, OAuth-refreshed), applies the inclusion
filter (non-blank zip, us_citizen, non-blank date_last_verified), runs the two
data-integrity checks, writes a full dataset plus a problems report per test, and
(optionally) uploads them to S3 (date-stamped) and emails the problem reports as
attachments via SES.

Local entry point: `main()`.  AWS Lambda entry point: `handler(event, context)`.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv

from nationbuilder_v2.extract import COLUMNS as EXTRACT_COLUMNS
from nationbuilder_v2.extract import extract_filtered_signups

# Load .env for local runs (no-op in Lambda, where env comes from the function
# config / Secrets Manager). Must run before the env vars below are read.
load_dotenv()


def _load_secret_into_env() -> None:
    """Populate NATIONBUILDER_* env vars from AWS Secrets Manager, if configured.

    When NATIONBUILDER_SECRET_ID is set (secret name or ARN), the secret's JSON
    value is read and each key copied into os.environ *without* overwriting an
    existing variable — so local `.env`/shell values still win, and in Lambda
    the secret supplies the OAuth credentials. No-op when unset.
    """
    secret_id = os.getenv("NATIONBUILDER_SECRET_ID")
    if not secret_id:
        return
    import json

    import boto3

    region = os.getenv("NATIONBUILDER_SECRET_REGION") or os.getenv("AWS_REGION")
    client = boto3.client("secretsmanager", region_name=region or None)
    raw = client.get_secret_value(SecretId=secret_id).get("SecretString") or ""
    raw = raw.lstrip("\ufeff")
    if raw.strip():
        for key, value in json.loads(raw).items():
            os.environ.setdefault(key, str(value))


_load_secret_into_env()


def _persist_refresh_token(new_refresh: str) -> None:
    """Persist a rotated refresh token.

    In Lambda (NATIONBUILDER_SECRET_ID set) write it back into the JSON secret so
    the next scheduled run can use it — /tmp and .env are not durable there.
    Locally (no secret id) fall back to rewriting .env.
    """
    secret_id = os.getenv("NATIONBUILDER_SECRET_ID")
    if secret_id:
        import json

        import boto3

        region = os.getenv("NATIONBUILDER_SECRET_REGION") or os.getenv("AWS_REGION")
        sm = boto3.client("secretsmanager", region_name=region or None)
        raw = sm.get_secret_value(SecretId=secret_id).get("SecretString") or "{}"
        data = json.loads(raw.lstrip("\ufeff") or "{}")
        data["NATIONBUILDER_REFRESH_TOKEN"] = new_refresh
        sm.put_secret_value(SecretId=secret_id, SecretString=json.dumps(data))
    else:
        from nationbuilder_v2.client import _persist_refresh_token_to_env

        _persist_refresh_token_to_env(new_refresh)
    os.environ["NATIONBUILDER_REFRESH_TOKEN"] = new_refresh

# -- Output location --------------------------------------------------------
OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")

# -- S3 upload (optional) ---------------------------------------------------
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "")
S3_REGION = os.getenv("S3_REGION", "")
PRESIGN_EXPIRY_SECONDS = int(os.getenv("PRESIGN_EXPIRY_SECONDS", str(7 * 24 * 3600)))

# -- SES email (optional) ---------------------------------------------------
SES_SENDER = os.getenv("SES_SENDER", "")
SES_REGION = os.getenv("SES_REGION", "") or S3_REGION
RECIPIENTS_S3_URI = os.getenv("RECIPIENTS_S3_URI", "")
RECIPIENTS_FILE = os.getenv("RECIPIENTS_FILE", "recipients.txt")
DEFAULT_EMAIL_MESSAGE = (
    "Each report is provided as both a CSV and an XLSX file. This is because of "
    "ZIP codes with leading zeros (e.g. 01002). The CSV stores the ZIP as text "
    "so the leading zero is preserved everywhere (text editors, Google Sheets, "
    "pandas), but Excel will strip it if you open the CSV directly. The XLSX "
    "version types the ZIP column as text so Excel keeps the leading zero. Use "
    "the XLSX in Excel; the CSV is the universal form for everything else."
)
EMAIL_MESSAGE = os.getenv("EMAIL_MESSAGE", DEFAULT_EMAIL_MESSAGE)

# NB pull concurrency (passed through to the V2 client).
NB_LOOKUP_CONCURRENCY = int(os.getenv("NB_LOOKUP_CONCURRENCY", "20"))

# -- Output filenames -------------------------------------------------------
OUTPUT_CSV = "zip_state_federal_district.csv"
OUTPUT_XLSX = "zip_state_federal_district.xlsx"
ZIP_STATE_PROBLEMS_CSV = "zip_state_problems.csv"
ZIP_STATE_PROBLEMS_XLSX = "zip_state_problems.xlsx"
FED_DISTRICT_PROBLEMS_CSV = "federal_district_problems.csv"
FED_DISTRICT_PROBLEMS_XLSX = "federal_district_problems.xlsx"
STATUS_FILE = "validate_zip_fed.status.txt"

# The full dataset is uploaded to S3 but never emailed (large / all records).
EMAIL_EXCLUDE_FILES = {OUTPUT_CSV, OUTPUT_XLSX}

ZIP_COLUMN = "registered_zip"

# -- ZIP reference workbook (USPS ZIP_Locale_Detail, three sheets merged) ----
ZIP_STATE_LOOKUP = "ZIP_Locale_Detail.xlsx"
ZIP_LOOKUP_S3_URI = os.getenv("ZIP_LOOKUP_S3_URI", "")
ZIP_MODE = os.getenv("ZIP_MODE", "delivery")
ZIP_LOOKUP_SHEETS = {
    "Detail": {"header": 0, "physical_zip": "PHYSICAL ZIP",
               "delivery_zip": "DELIVERY ZIPCODE", "state": "PHYSICAL STATE"},
    "Unique": {"header": 2, "physical_zip": "ZIP",
               "delivery_zip": "ZIPCODE", "state": "STATE"},
    "Other":  {"header": 2, "physical_zip": "ZIP",
               "delivery_zip": "ZIPCODE", "state": "STATE"},
}

RUN_DATE = datetime.now(timezone.utc).strftime("%Y%m%d")
SES_MAX_ATTACH_BYTES = 7_000_000

pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)


# ---------------------------------------------------------------------------
# Small helpers (self-contained; no Insights/Tableau dependency)
# ---------------------------------------------------------------------------
def _out(filename: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


def _dated_name(filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    return f"{stem}_{RUN_DATE}{ext}"


def _write_status(lines: list[str]) -> None:
    with open(_out(STATUS_FILE), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    without = uri[len("s3://"):]
    bucket, _, key = without.partition("/")
    return bucket, key


def resolve_reference_file() -> str:
    """Local path to the ZIP reference workbook (download from S3 if configured)."""
    if not ZIP_LOOKUP_S3_URI:
        return ZIP_STATE_LOOKUP
    import boto3
    bucket, key = _parse_s3_uri(ZIP_LOOKUP_S3_URI)
    local_path = _out(os.path.basename(key) or ZIP_STATE_LOOKUP)
    boto3.client("s3", region_name=S3_REGION or None).download_file(bucket, key, local_path)
    return local_path


def build_zip_to_state(reference_file: str, mode: str = ZIP_MODE) -> dict[int, str]:
    """Build {zip:int -> state} from the three sheets (Detail wins on conflicts)."""
    zip_key = "physical_zip" if mode == "physical" else "delivery_zip"
    zip_to_state: dict[int, str] = {}
    for sheet, cols in ZIP_LOOKUP_SHEETS.items():
        frame = pd.read_excel(reference_file, sheet_name=sheet, header=cols["header"])
        zips = pd.to_numeric(frame[cols[zip_key]], errors="coerce")
        states = frame[cols["state"]].astype(str).str.strip().str.upper()
        for z, s in zip(zips, states):
            if pd.notna(z) and s and s != "NAN":
                zip_to_state.setdefault(int(z), s)
    return zip_to_state


def _write_xlsx_zip_as_text(frame: pd.DataFrame, path: str) -> None:
    """Write a DataFrame to .xlsx, typing the ZIP column as text (keeps 0-prefix)."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="data")
        worksheet = writer.sheets["data"]
        cols = list(frame.columns)
        if ZIP_COLUMN in cols:
            col_idx = cols.index(ZIP_COLUMN) + 1
            for row in range(2, len(frame) + 2):
                worksheet.cell(row=row, column=col_idx).number_format = "@"


def upload_to_s3(paths: list[str]) -> list[str]:
    """Upload each file to S3 with a date-stamped object name; return presigned URLs."""
    if not S3_BUCKET:
        return []
    import boto3
    s3 = boto3.client("s3", region_name=S3_REGION or None)
    prefix = S3_PREFIX.strip("/")
    urls: list[str] = []
    for path in paths:
        object_name = _dated_name(os.path.basename(path))
        key = f"{prefix}/{object_name}" if prefix else object_name
        s3.upload_file(path, S3_BUCKET, key)
        urls.append(
            s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": S3_BUCKET, "Key": key},
                ExpiresIn=PRESIGN_EXPIRY_SECONDS,
            )
        )
    return urls


def load_recipients() -> list[str]:
    """Email recipients from S3 (RECIPIENTS_S3_URI) or local RECIPIENTS_FILE."""
    text = ""
    if RECIPIENTS_S3_URI:
        import boto3
        bucket, key = _parse_s3_uri(RECIPIENTS_S3_URI)
        obj = boto3.client("s3", region_name=S3_REGION or None).get_object(Bucket=bucket, Key=key)
        text = obj["Body"].read().decode("utf-8")
    elif os.path.exists(RECIPIENTS_FILE):
        with open(RECIPIENTS_FILE, encoding="utf-8") as fh:
            text = fh.read()
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _email_body(summary: dict, attached: list[str], skipped: list[str]) -> str:
    lines = [
        "The nightly validation run has completed.",
        "",
        f"Signups processed (after filter): {summary.get('kept', 'n/a')}",
        f"ZIP / state problems:             {summary.get('zip_state_problems', 'n/a')}",
        f"Federal district problems:        {summary.get('federal_district_problems', 'n/a')}",
    ]
    if EMAIL_MESSAGE.strip():
        lines += ["", EMAIL_MESSAGE.strip()]
    if attached:
        lines += ["", "Attached problem reports:"] + [f"  {n}" for n in attached]
    if skipped:
        lines += ["", "Not attached (too large; in S3):"] + [f"  {n}" for n in skipped]
    lines += ["", "These files contain member PII. Do not forward this email."]
    return "\n".join(lines)


def notify_by_email(files: list[str], summary: dict) -> list[str]:
    """Email the problem-report files as attachments via SES. Returns recipients."""
    if not SES_SENDER:
        return []
    recipients = load_recipients()
    if not recipients:
        return []
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import boto3

    attach_paths = [p for p in files if os.path.basename(p) not in EMAIL_EXCLUDE_FILES]
    attached_names, skipped_names, parts, total = [], [], [], 0
    for path in attach_paths:
        size = os.path.getsize(path)
        dated = _dated_name(os.path.basename(path))
        if total + size > SES_MAX_ATTACH_BYTES:
            skipped_names.append(dated)
            continue
        with open(path, "rb") as fh:
            part = MIMEApplication(fh.read())
        part.add_header("Content-Disposition", "attachment", filename=dated)
        parts.append(part)
        attached_names.append(dated)
        total += size

    msg = MIMEMultipart()
    msg["Subject"] = "ZIP / State / Federal District validation report"
    msg["From"] = SES_SENDER
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(_email_body(summary, attached_names, skipped_names)))
    for part in parts:
        msg.attach(part)

    boto3.client("ses", region_name=SES_REGION or None).send_raw_email(
        Source=SES_SENDER,
        Destinations=recipients,
        RawMessage={"Data": msg.as_string()},
    )
    return recipients


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------
def _zip_state_result(row: pd.Series, zip_to_state: dict[int, str]) -> str:
    zip_val = pd.to_numeric(row["registered_zip"], errors="coerce")
    state = str(row["registered_state"]).strip().upper()
    if pd.isna(zip_val):
        return "ZIP STATE PROBLEM. ZIP IS BLANK"
    expected = zip_to_state.get(int(zip_val))
    if expected is None:
        return "ZIP STATE PROBLEM. ZIP NOT FOUND"
    if expected == state:
        return ""
    return f"ZIP STATE PROBLEM. SHOULD BE {expected}"


_FED_PREFIX = re.compile(r"^\s*([A-Za-z]{2})")


def _federal_district_result(row: pd.Series) -> str:
    state = str(row["registered_state"]).strip().upper()
    fd = str(row["federal_district"] or "").strip()
    if fd == "":
        return "FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK"
    m = _FED_PREFIX.match(fd)
    prefix = m.group(1).upper() if m else ""
    if prefix and prefix == state:
        return ""
    return "FEDERAL DISTRICT PROBLEM"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run() -> dict:
    """Pull from V2, filter, validate, write reports, optionally upload + email."""
    lines: list[str] = []
    result: dict = {"status": "ERROR", "files": [], "presigned_urls": []}
    try:
        reference_file = resolve_reference_file()
        zip_to_state = build_zip_to_state(reference_file, ZIP_MODE)

        rows, filter_stats = extract_filtered_signups(
            concurrency=NB_LOOKUP_CONCURRENCY, on_refresh=_persist_refresh_token
        )
        df = pd.DataFrame(rows, columns=EXTRACT_COLUMNS)

        # Run the two checks.
        df["zip_state_problem"] = df.apply(
            lambda r: _zip_state_result(r, zip_to_state), axis=1
        )
        df["federal_district_problem"] = df.apply(_federal_district_result, axis=1)

        # Zero-pad the ZIP for display (after the numeric check).
        def _pad(v: object) -> object:
            s = "" if v is None else str(v).strip()
            return f"{int(s):05d}" if s.isdigit() else s
        df[ZIP_COLUMN] = df[ZIP_COLUMN].map(_pad)

        # Report summary lines.
        lines.append(f"Source: NationBuilder V2 API (slug {os.getenv('NATIONBUILDER_SLUG')})")
        lines.append(
            f"ZIP reference: {ZIP_STATE_LOOKUP} "
            f"[sheets: {', '.join(ZIP_LOOKUP_SHEETS)}] mode={ZIP_MODE} "
            f"({len(zip_to_state):,} ZIP keys)"
        )
        lines.append("Filter stats:")
        for k, v in filter_stats.items():
            lines.append(f"  {k}: {v}")

        zs_counts = df["zip_state_problem"].value_counts()
        fd_counts = df["federal_district_problem"].value_counts()
        zs_ok = int(zs_counts.get("", 0))
        fd_ok = int(fd_counts.get("", 0))
        lines.append("")
        lines.append(f"ZIP/state: {len(df) - zs_ok} problems of {len(df)}")
        for label, n in zs_counts[zs_counts.index != ""].head(15).items():
            lines.append(f"    {n:>7}  {label}")
        lines.append(f"Federal district: {len(df) - fd_ok} problems of {len(df)}")
        for label, n in fd_counts[fd_counts.index != ""].head(15).items():
            lines.append(f"    {n:>7}  {label}")

        # Per-test problem subsets (each keeps only its own flag column).
        zip_state_problems = df[df["zip_state_problem"] != ""].drop(
            columns=["federal_district_problem"]
        )
        fed_district_problems = df[df["federal_district_problem"] != ""].drop(
            columns=["zip_state_problem"]
        )

        written: list[str] = []

        full_csv, full_xlsx = _out(OUTPUT_CSV), _out(OUTPUT_XLSX)
        df.to_csv(full_csv, index=False)
        _write_xlsx_zip_as_text(df, full_xlsx)
        written += [full_csv, full_xlsx]

        zs_csv, zs_xlsx = _out(ZIP_STATE_PROBLEMS_CSV), _out(ZIP_STATE_PROBLEMS_XLSX)
        zip_state_problems.to_csv(zs_csv, index=False)
        _write_xlsx_zip_as_text(zip_state_problems, zs_xlsx)
        written += [zs_csv, zs_xlsx]

        fd_csv, fd_xlsx = _out(FED_DISTRICT_PROBLEMS_CSV), _out(FED_DISTRICT_PROBLEMS_XLSX)
        fed_district_problems.to_csv(fd_csv, index=False)
        _write_xlsx_zip_as_text(fed_district_problems, fd_xlsx)
        written += [fd_csv, fd_xlsx]

        lines.append("")
        lines.append(f"Wrote {len(written)} files to {OUTPUT_DIR}")

        result.update(
            status="OK",
            kept=int(len(df)),
            zip_state_problems=int(len(zip_state_problems)),
            federal_district_problems=int(len(fed_district_problems)),
            filter_stats=filter_stats,
            files=written,
        )

        if S3_BUCKET:
            urls = upload_to_s3(written)
            result["presigned_urls"] = urls
            lines.append(
                f"Uploaded {len(urls)} dated file(s) to "
                f"s3://{S3_BUCKET}/{S3_PREFIX.strip('/')} (run date {RUN_DATE})"
            )

        if SES_SENDER:
            emailed = notify_by_email(written, result)
            result["emailed_to"] = emailed
            lines.append(
                f"Emailed report attachments to: {', '.join(emailed)}"
                if emailed else "Email skipped (no recipients)."
            )

        lines.append("STATUS: OK")
    except Exception as exc:  # noqa: BLE001
        import traceback
        result["error"] = repr(exc)
        lines.append("STATUS: ERROR")
        lines.append(repr(exc))
        lines.append(traceback.format_exc())

    _write_status(lines)
    print("\n".join(lines))
    return result


def main() -> None:
    run()


def handler(event, context):  # noqa: ANN001
    result = run()
    return {"statusCode": 200 if result.get("status") == "OK" else 500, "result": result}


if __name__ == "__main__":
    main()
