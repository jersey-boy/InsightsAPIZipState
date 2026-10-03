"""
Pull the actual data behind the "Zip State Federal District" Insights view.

This view lives in the workbook `APItemplate_ZipStateAndFedDistrict`
(contentUrl: APItemplate_ZipStateAndFedDistrict/sheets/ZipStateTest).

We fetch the view's underlying data as a pandas DataFrame via the Tableau
REST API, print a preview, and save the full result to CSV.

Run:
    .venv/bin/python pull_zip_state_federal.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
from tableau_api_lib.utils.querying import get_view_data_dataframe

from nb_insights import insights_connection

# The "Zip State Federal District" view (from reports_views.csv).
VIEW_ID = "c7f541b2-87db-4fad-97c5-2e532dbbe956"
VIEW_NAME = "Zip State Federal District"

# Where output files are written. Defaults to the current directory for local
# runs; set OUTPUT_DIR to "/tmp" in Lambda (the only writable path there).
OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")

# Optional S3 upload. When S3_BUCKET is set, every output file is uploaded to
# s3://<bucket>/<prefix>/<filename> and a presigned download URL is generated.
# When it is unset (the default), the script only writes local files. In Lambda
# these come from function env vars; locally they can come from .env.
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "")  # e.g. "zip-state-reports/"
S3_REGION = os.getenv("S3_REGION", "")  # optional; boto3 default region if unset
# Presigned URL lifetime in seconds (default 7 days; S3's max is 7 days).
PRESIGN_EXPIRY_SECONDS = int(os.getenv("PRESIGN_EXPIRY_SECONDS", str(7 * 24 * 3600)))

# Optional NationBuilder cross-check. The Insights "federal_district" value is
# unreliable, so for records flagged by the federal-district test we look up the
# authoritative value via the NationBuilder V1 API and show both side by side.
# Enabled when both NB vars are set; otherwise the cross-check is skipped and
# the report is produced exactly as before. The lookup only hits the API for the
# flagged signup_ids (one GET per id), not the whole nation.
NB_SLUG = os.getenv("NATIONBUILDER_SLUG", "")
NB_ACCESS_TOKEN = os.getenv("NATIONBUILDER_ACCESS_TOKEN", "")
# Concurrency for the per-id NB lookups. Each call is ~0.45s, so the flagged
# set (~1,000 ids) is ~7 min serially; a modest thread pool brings it under a
# minute. The NB client already retries on 429, so concurrency is safe within
# reason. Tune via NB_LOOKUP_CONCURRENCY.
NB_LOOKUP_CONCURRENCY = int(os.getenv("NB_LOOKUP_CONCURRENCY", "10"))

# Optional email notification via Amazon SES. When SES_SENDER is set (a verified
# SES identity, e.g. "insights-reports@lategothikdata.com") AND a recipient list
# is available, the presigned download links are emailed after upload.
# Recipients come from a plain-text file (one address per line, "#" comments),
# sourced either from S3 (RECIPIENTS_S3_URI) or a local path (RECIPIENTS_FILE).
# All email is skipped when SES_SENDER is unset, so local runs are unaffected.
SES_SENDER = os.getenv("SES_SENDER", "")
SES_REGION = os.getenv("SES_REGION", "") or S3_REGION
RECIPIENTS_S3_URI = os.getenv("RECIPIENTS_S3_URI", "")
RECIPIENTS_FILE = os.getenv("RECIPIENTS_FILE", "recipients.txt")

# Free-text message included in the email body (between the counts and the
# links). Override with the EMAIL_MESSAGE env var; the default explains why both
# CSV and XLSX versions are provided (the leading-zero ZIP issue). Set to an
# empty string to omit the message entirely.
DEFAULT_EMAIL_MESSAGE = (
    "Each report is provided as both a CSV and an XLSX file. This is because of "
    "ZIP codes with leading zeros (e.g. 01002). The CSV stores the ZIP as text "
    "so the leading zero is preserved everywhere (text editors, Google Sheets, "
    "pandas), but Excel will strip it if you open the CSV directly. The XLSX "
    "version types the ZIP column as text so Excel keeps the leading zero. Use "
    "the XLSX in Excel; the CSV is the universal form for everything else."
)
EMAIL_MESSAGE = os.getenv("EMAIL_MESSAGE", DEFAULT_EMAIL_MESSAGE)

# Full dataset (every record, both problem-flag columns).
OUTPUT_CSV = "zip_state_federal_district.csv"
OUTPUT_XLSX = "zip_state_federal_district.xlsx"

# Test 1 — ZIP / state problems only.
ZIP_STATE_PROBLEMS_CSV = "zip_state_problems.csv"
ZIP_STATE_PROBLEMS_XLSX = "zip_state_problems.xlsx"

# Test 2 — federal district / state problems only.
FED_DISTRICT_PROBLEMS_CSV = "federal_district_problems.csv"
FED_DISTRICT_PROBLEMS_XLSX = "federal_district_problems.xlsx"

# ZIP -> state reference is the USPS "ZIP_Locale_Detail" workbook. It has three
# sheets that together cover all ZIPs:
#   Detail  - the main locale list          (clean header on row 1)
#   Unique  - unique/single-entity ZIPs      (multi-row header; real header row 3)
#   Other   - remaining ZIPs                  (multi-row header; real header row 3)
# The Detail sheet alone omits ~195 valid ZIPs (universities, PO-box-only,
# single-entity ZIPs) that live on Unique/Other, so we merge all three.
ZIP_STATE_LOOKUP = "ZIP_Locale_Detail.xlsx"

# Optionally source the reference workbook from S3. When ZIP_LOOKUP_S3_URI is
# set (e.g. "s3://my-bucket/refs/ZIP_Locale_Detail.xlsx"), it is downloaded to
# OUTPUT_DIR at runtime and used in place of a bundled local copy. This keeps
# the ~4 MB file out of the deployment package. Unset -> use the local file.
ZIP_LOOKUP_S3_URI = os.getenv("ZIP_LOOKUP_S3_URI", "")

# Which ZIP/state pair to validate against. Set ZIP_MODE to "physical" or
# "delivery". The workbook only carries a physical state, so delivery mode
# validates against that physical state, keyed by the delivery ZIP.
#   physical -> key on the physical ZIP
#   delivery -> key on the delivery ZIP
ZIP_MODE = "delivery"

# Per-sheet column names. Detail has a clean single-row header; Unique/Other
# have a 3-row header (real header at row index 2) that collapses the split
# "PHYSICAL STATE"/"PHYSICAL ZIP" labels down to "STATE"/"ZIP", and names the
# delivery ZIP column "ZIPCODE".
ZIP_LOOKUP_SHEETS = {
    "Detail": {
        "header": 0,
        "physical_zip": "PHYSICAL ZIP",
        "delivery_zip": "DELIVERY ZIPCODE",
        "state": "PHYSICAL STATE",
    },
    "Unique": {
        "header": 2,
        "physical_zip": "ZIP",
        "delivery_zip": "ZIPCODE",
        "state": "STATE",
    },
    "Other": {
        "header": 2,
        "physical_zip": "ZIP",
        "delivery_zip": "ZIPCODE",
        "state": "STATE",
    },
}

# Column that holds the ZIP; written as zero-padded text for universal CSV
# readers, and typed as text in the .xlsx so Excel keeps the leading zeros.
ZIP_COLUMN = "registered_zip_clean"

pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)


STATUS_FILE = "pull_zip_state_federal.status.txt"


def _out(filename: str) -> str:
    """Resolve an output filename against OUTPUT_DIR, creating the dir if needed."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


# Run date (UTC) used to stamp S3 object names and email attachments so a
# history accumulates in S3 (e.g. zip_state_problems_20260928.csv). Computed
# once per process so every file in a run shares the same stamp.
RUN_DATE = datetime.now(timezone.utc).strftime("%Y%m%d")


def _dated_name(filename: str) -> str:
    """Insert the run date before the extension: foo.csv -> foo_YYYYMMDD.csv."""
    stem, ext = os.path.splitext(filename)
    return f"{stem}_{RUN_DATE}{ext}"


# SES caps a message (headers + body + attachments) at 10 MB, and base64
# encoding inflates attachments ~37%, so keep a conservative raw-bytes budget.
SES_MAX_ATTACH_BYTES = 7_000_000


def _write_status(lines: list[str]) -> None:
    """Write status/preview to a workspace file (terminal capture is flaky here)."""
    with open(_out(STATUS_FILE), "w") as fh:
        fh.write("\n".join(lines) + "\n")


def upload_to_s3(paths: list[str]) -> list[str]:
    """Upload each local file to S3 and return a presigned URL per file.

    No-op returning [] when S3_BUCKET is unset. Uses the ambient AWS credentials
    (an IAM role in Lambda, or the local AWS profile/env when run on a laptop),
    so no keys are handled here.
    """
    if not S3_BUCKET:
        return []

    import boto3  # imported lazily so local runs without S3 don't need it

    s3 = boto3.client("s3", region_name=S3_REGION or None)
    prefix = S3_PREFIX.strip("/")
    urls: list[str] = []
    for path in paths:
        # Date-stamp the S3 object name so a history builds up in the bucket
        # (the local file keeps its plain name; only the S3 key is dated).
        object_name = _dated_name(os.path.basename(path))
        key = f"{prefix}/{object_name}" if prefix else object_name
        s3.upload_file(path, S3_BUCKET, key)
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=PRESIGN_EXPIRY_SECONDS,
        )
        urls.append(url)
    return urls


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split an s3://bucket/key URI into (bucket, key)."""
    without_scheme = uri[len("s3://") :]
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


def load_recipients() -> list[str]:
    """Return the list of email recipients from S3 or a local text file.

    The file is one address per line; blank lines and lines starting with "#"
    are ignored. Prefers RECIPIENTS_S3_URI when set, else RECIPIENTS_FILE.
    Returns [] if no source is available (which disables email).
    """
    text = ""
    if RECIPIENTS_S3_URI:
        import boto3  # lazy

        bucket, key = _parse_s3_uri(RECIPIENTS_S3_URI)
        obj = boto3.client("s3", region_name=S3_REGION or None).get_object(
            Bucket=bucket, Key=key
        )
        text = obj["Body"].read().decode("utf-8")
    elif os.path.exists(RECIPIENTS_FILE):
        with open(RECIPIENTS_FILE, encoding="utf-8") as fh:
            text = fh.read()

    recipients = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            recipients.append(line)
    return recipients


# Files uploaded to S3 for reference but NOT linked in the notification email.
# The full dataset is large and not needed for the day-to-day problem review;
# only the two problem reports are emailed.
EMAIL_EXCLUDE_FILES = {OUTPUT_CSV, OUTPUT_XLSX}


EMAIL_SUBJECT = "ZIP / State / Federal District validation report"


def _email_body(summary: dict, attached: list[str], skipped: list[str]) -> str:
    """Compose the plain-text email body (counts + message + attachment notes)."""
    lines = [
        "The nightly validation run has completed.",
        "",
        f"Records processed:            {summary.get('rows', 'n/a')}",
        f"ZIP / state problems:         {summary.get('zip_state_problems', 'n/a')}",
        f"Federal district problems:    {summary.get('federal_district_problems', 'n/a')}",
    ]
    if EMAIL_MESSAGE.strip():
        lines += ["", EMAIL_MESSAGE.strip()]
    if attached:
        lines += ["", "Attached problem reports:"]
        lines += [f"  {name}" for name in attached]
    if skipped:
        lines += [
            "",
            "Not attached (too large for email; available in S3):",
        ]
        lines += [f"  {name}" for name in skipped]
    lines += [
        "",
        "The full dataset is not attached; it is available in S3 for reference.",
        "These files contain member PII. Do not forward this email.",
    ]
    return "\n".join(lines)


def notify_by_email(files: list[str], summary: dict) -> list[str]:
    """Email the problem-report files as attachments via SES. Returns recipients.

    No-op returning [] when SES_SENDER is unset or the recipient list is empty,
    so local runs and un-configured deploys are unaffected. Only the problem
    reports are attached (the full dataset, EMAIL_EXCLUDE_FILES, is uploaded to
    S3 but never emailed). Attachments are named with the run date and skipped
    (with a note in the body) if they would push the message past the SES size
    limit. The email body carries counts + message only — never inline data.

    NOTE: an earlier version emailed presigned S3 *links* instead of
    attachments. That approach is preserved in `_build_link_email()` below in
    case we want to switch back; it is not called.
    """
    if not SES_SENDER:
        return []
    recipients = load_recipients()
    if not recipients:
        return []

    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    import boto3  # lazy

    # Attach the problem reports only, within the SES size budget.
    attach_paths = [
        p for p in files if os.path.basename(p) not in EMAIL_EXCLUDE_FILES
    ]
    attached_names: list[str] = []
    skipped_names: list[str] = []
    total = 0
    parts: list[MIMEApplication] = []
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
    msg["Subject"] = EMAIL_SUBJECT
    msg["From"] = SES_SENDER
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(_email_body(summary, attached_names, skipped_names)))
    for part in parts:
        msg.attach(part)

    ses = boto3.client("ses", region_name=SES_REGION or None)
    ses.send_raw_email(
        Source=SES_SENDER,
        Destinations=recipients,
        RawMessage={"Data": msg.as_string()},
    )
    return recipients


def _build_link_email(urls: list[str], summary: dict) -> tuple[str, str]:
    """ALTERNATIVE (not currently used): body for emailing presigned links.

    Kept for easy revert to the link-based notification. Returns (subject, body).
    To use, replace the send in notify_by_email() with ses.send_email() using
    this body, passing the presigned URLs from upload_to_s3(). Links exclude the
    full-dataset files the same way attachments do.
    """
    email_urls = [
        url
        for url in urls
        if url.split("?", 1)[0].rsplit("/", 1)[-1] not in EMAIL_EXCLUDE_FILES
    ]
    lines = [
        "The nightly validation run has completed.",
        "",
        f"Records processed:            {summary.get('rows', 'n/a')}",
        f"ZIP / state problems:         {summary.get('zip_state_problems', 'n/a')}",
        f"Federal district problems:    {summary.get('federal_district_problems', 'n/a')}",
    ]
    if EMAIL_MESSAGE.strip():
        lines += ["", EMAIL_MESSAGE.strip()]
    lines += [
        "",
        f"Problem report download links (valid ~{PRESIGN_EXPIRY_SECONDS / 86400:g} days):",
    ]
    for url in email_urls:
        name = url.split("?", 1)[0].rsplit("/", 1)[-1]
        lines += [f"  {name}:", f"    {url}"]
    lines += ["", "These files contain member PII. Do not forward the links."]
    return EMAIL_SUBJECT, "\n".join(lines)


def resolve_reference_file() -> str:
    """Return the local path to the ZIP reference workbook.

    When ZIP_LOOKUP_S3_URI is set, download it into OUTPUT_DIR and return that
    path; otherwise return the bundled/local ZIP_STATE_LOOKUP filename as-is.
    """
    if not ZIP_LOOKUP_S3_URI:
        return ZIP_STATE_LOOKUP

    import boto3  # lazy; only needed when sourcing the reference from S3

    bucket, key = _parse_s3_uri(ZIP_LOOKUP_S3_URI)
    local_path = _out(os.path.basename(key) or ZIP_STATE_LOOKUP)
    boto3.client("s3", region_name=S3_REGION or None).download_file(
        bucket, key, local_path
    )
    return local_path


def nb_federal_districts(signup_ids: list[int]) -> dict[int, str]:
    """Look up the NationBuilder federal_district for each signup id.

    Returns {signup_id -> federal_district_or_marker}. The marker is "" when NB
    has no district, "NOT FOUND" when the id doesn't exist in NationBuilder, and
    "LOOKUP ERROR" when a call fails — so the report always has a value to show.
    No-op returning {} when NB credentials are not configured.

    Only the ids passed in (the flagged records) are queried, one GET each.
    """
    if not (NB_SLUG and NB_ACCESS_TOKEN) or not signup_ids:
        return {}

    from concurrent.futures import ThreadPoolExecutor

    from nationbuilder import (  # lazy; only needed for the cross-check
        NationBuilderClient,
        NotFoundError,
        NationBuilderError,
    )

    # One shared client; httpx.Client is safe to use across threads.
    client = NationBuilderClient(slug=NB_SLUG, access_token=NB_ACCESS_TOKEN)

    def _one(sid: int) -> tuple[int, str]:
        try:
            district = client.get_person(sid).get_raw("federal_district")
            return sid, ("" if district is None else str(district).strip())
        except NotFoundError:
            return sid, "NOT FOUND"
        except NationBuilderError:
            return sid, "LOOKUP ERROR"

    result: dict[int, str] = {}
    try:
        workers = max(1, NB_LOOKUP_CONCURRENCY)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for sid, value in pool.map(_one, signup_ids):
                result[sid] = value
    finally:
        client.close()
    return result


def build_zip_to_state(reference_file: str, mode: str = ZIP_MODE) -> dict[int, str]:
    """Build a {zip -> state} lookup from all three sheets of the reference.

    `mode` selects which ZIP column keys the lookup ("physical" or "delivery").
    The Detail sheet wins on conflicts because it is the authoritative locale
    list; Unique/Other only fill in ZIPs Detail doesn't already cover.
    """
    zip_key = "physical_zip" if mode == "physical" else "delivery_zip"
    zip_to_state: dict[int, str] = {}
    # Iterate Detail first so its mappings take precedence via setdefault.
    for sheet, cols in ZIP_LOOKUP_SHEETS.items():
        frame = pd.read_excel(
            reference_file, sheet_name=sheet, header=cols["header"]
        )
        zips = pd.to_numeric(frame[cols[zip_key]], errors="coerce")
        states = frame[cols["state"]].astype(str).str.strip().str.upper()
        for z, s in zip(zips, states):
            if pd.notna(z) and s and s != "NAN":
                zip_to_state.setdefault(int(z), s)
    return zip_to_state


def _write_xlsx_zip_as_text(frame: pd.DataFrame, path: str) -> None:
    """Write a DataFrame to .xlsx, forcing the ZIP column to Excel text format.

    Excel shows the ZIP with its leading zeros natively (no ="..." trick and no
    manual import step) because the cells are typed as text ("@" number format).
    """
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="data")
        worksheet = writer.sheets["data"]
        # +1 because openpyxl columns are 1-indexed; header is row 1.
        zip_col_idx = list(frame.columns).index(ZIP_COLUMN) + 1
        for row in range(2, len(frame) + 2):
            worksheet.cell(row=row, column=zip_col_idx).number_format = "@"


def run() -> dict:
    """Run the full pipeline: pull, validate, write files, optionally upload.

    Returns a result dict describing the run (status, row/problem counts, the
    files written, and any presigned URLs). Never raises: failures are captured
    in the returned dict and the status file, so a Lambda gets a clean response.
    """
    lines: list[str] = []
    result: dict = {"status": "ERROR", "files": [], "presigned_urls": []}
    try:
        reference_file = resolve_reference_file()

        with insights_connection() as conn:
            df = get_view_data_dataframe(conn, view_id=VIEW_ID)

        # Drop personally identifying columns before any output is produced.
        # signup_id is kept as a non-PII identifier so flagged records can still
        # be traced back in NationBuilder.
        PII_COLUMNS = ["full_name"]
        df = df.drop(columns=[c for c in PII_COLUMNS if c in df.columns])

        # Reorder columns for the output CSV. Any columns present in the data
        # but not listed here are appended afterwards so nothing is dropped.
        column_order = [
            "signup_id",
            "registered_state",
            "registered_zip_clean",
            "federal_district",
            "fed_dist_prefix",
        ]
        ordered = [c for c in column_order if c in df.columns]
        remaining = [c for c in df.columns if c not in ordered]
        df = df[ordered + remaining]

        # Add two new problem-flag fields (populated by the checks below).
        df["zip_state_problem"] = ""
        df["federal_district_problem"] = ""

        # --- Zip/state validation ------------------------------------------
        # Build a zip -> state lookup from all three sheets of the reference.
        # Zips are matched as integers on both sides, which keeps leading-zero
        # zips consistent (the view stores registered_zip_clean as a number,
        # e.g. 1002).
        zip_to_state = build_zip_to_state(reference_file, ZIP_MODE)

        def _zip_state_result(row: pd.Series) -> str:
            zip_val = pd.to_numeric(row["registered_zip_clean"], errors="coerce")
            state = str(row["registered_state"]).strip().upper()
            if pd.isna(zip_val):
                return "ZIP STATE PROBLEM. ZIP IS BLANK"
            expected = zip_to_state.get(int(zip_val))
            if expected is None:
                return "ZIP STATE PROBLEM. ZIP NOT FOUND"
            if expected == state:
                return ""
            return f"ZIP STATE PROBLEM. SHOULD BE {expected}"

        df["zip_state_problem"] = df.apply(_zip_state_result, axis=1)

        # --- Federal district validation -----------------------------------
        # fed_dist_prefix is the 2-letter state code for the federal district
        # (e.g. federal_district "AK0" has prefix "AK"). It should match the
        # member's registered_state.
        def _federal_district_result(row: pd.Series) -> str:
            state = str(row["registered_state"]).strip().upper()
            prefix = row["fed_dist_prefix"]
            if pd.isna(prefix) or str(prefix).strip() == "":
                return "FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK"
            if str(prefix).strip().upper() == state:
                return ""
            return "FEDERAL DISTRICT PROBLEM"

        df["federal_district_problem"] = df.apply(_federal_district_result, axis=1)

        # fed_dist_prefix was only needed to run the federal district test;
        # drop it so it doesn't appear in the output files.
        df = df.drop(columns=["fed_dist_prefix"])

        # Format the ZIP as zero-padded 5-digit TEXT (e.g. 1002 -> "01002").
        # This is the universal form: text editors, Sublime, Google Sheets and
        # pandas all show the leading zeros correctly. Excel-on-double-click
        # would still coerce plain text to a number, which is why we also emit
        # an .xlsx below with this column explicitly typed as text.
        # (Runs AFTER both validation checks, which use the numeric value.)
        def _pad_zip(value: object) -> object:
            if pd.isna(value):
                return value
            return f"{int(value):05d}"

        df[ZIP_COLUMN] = df[ZIP_COLUMN].map(_pad_zip)

        lines.append(f"View: {VIEW_NAME}  (id={VIEW_ID})")
        lines.append(
            f"ZIP reference: {ZIP_STATE_LOOKUP} "
            f"[sheets: {', '.join(ZIP_LOOKUP_SHEETS)}] "
            f"mode={ZIP_MODE}  ({len(zip_to_state):,} ZIP keys)"
        )
        lines.append(f"Rows: {len(df)}   Columns: {len(df.columns)}")
        lines.append(f"Columns: {list(df.columns)}")
        lines.append("")
        lines.append("zip_state_problem breakdown:")
        counts = df["zip_state_problem"].value_counts()
        ok = int(counts.get("", 0))
        lines.append(f"  No problem (blank): {ok}")
        lines.append(f"  Problems: {len(df) - ok}")
        # Show the 15 most common problem messages.
        problem_counts = counts[counts.index != ""]
        for label, n in problem_counts.head(15).items():
            lines.append(f"    {n:>7}  {label}")
        lines.append("")
        lines.append("federal_district_problem breakdown:")
        fd_counts = df["federal_district_problem"].value_counts()
        fd_ok = int(fd_counts.get("", 0))
        lines.append(f"  No problem (blank): {fd_ok}")
        lines.append(f"  Problems: {len(df) - fd_ok}")
        for label, n in fd_counts[fd_counts.index != ""].head(15).items():
            lines.append(f"    {n:>7}  {label}")
        lines.append("")
        lines.append("First 20 rows:")
        lines.append(df.head(20).to_string(index=False))

        # Two separate problem reports, one per test. Each keeps only the flag
        # column relevant to its own test so the report is self-contained.
        zip_state_problems = df[df["zip_state_problem"] != ""].drop(
            columns=["federal_district_problem"]
        )
        fed_district_problems = df[df["federal_district_problem"] != ""].drop(
            columns=["zip_state_problem"]
        )

        # NationBuilder cross-check: the Insights federal_district is unreliable,
        # so for the flagged records look up the authoritative value from the NB
        # API and show both side by side. Adds two columns:
        #   nb_federal_district      - the value NationBuilder has for that id
        #   federal_district_match   - MATCH / MISMATCH between Insights and NB
        # Only runs when NB credentials are configured; otherwise the report is
        # unchanged.
        if NB_SLUG and NB_ACCESS_TOKEN and len(fed_district_problems) > 0:
            flagged_ids = [
                int(x)
                for x in pd.to_numeric(
                    fed_district_problems["signup_id"], errors="coerce"
                ).dropna()
            ]
            nb_map = nb_federal_districts(flagged_ids)
            fed_district_problems = fed_district_problems.copy()

            def _nb_value(row: pd.Series) -> str:
                sid = pd.to_numeric(row["signup_id"], errors="coerce")
                if pd.isna(sid):
                    return ""
                return nb_map.get(int(sid), "")

            def _match(row: pd.Series) -> str:
                insights = str(row["federal_district"]).strip().upper()
                nb = str(row["nb_federal_district"]).strip().upper()
                # Don't label a comparison when NB couldn't be read.
                if nb in ("", "NOT FOUND", "LOOKUP ERROR"):
                    return ""
                return "MATCH" if insights == nb else "MISMATCH"

            fed_district_problems["nb_federal_district"] = fed_district_problems.apply(
                _nb_value, axis=1
            )
            fed_district_problems["federal_district_match"] = (
                fed_district_problems.apply(_match, axis=1)
            )
            lines.append("")
            mm = int((fed_district_problems["federal_district_match"] == "MISMATCH").sum())
            ma = int((fed_district_problems["federal_district_match"] == "MATCH").sum())
            lines.append(
                f"NationBuilder cross-check (flagged ids): "
                f"{ma} match, {mm} mismatch, "
                f"{len(fed_district_problems) - ma - mm} not comparable."
            )

        # Collect every file we write so we can (optionally) upload them to S3.
        written: list[str] = []

        # Full dataset: universal CSV + Excel-friendly XLSX (keeps both flags).
        full_csv, full_xlsx = _out(OUTPUT_CSV), _out(OUTPUT_XLSX)
        df.to_csv(full_csv, index=False)
        _write_xlsx_zip_as_text(df, full_xlsx)
        written += [full_csv, full_xlsx]
        lines.append("")
        lines.append(f"Saved full data ({len(df)} rows):")
        lines.append(f"  {full_csv}   (universal, ZIP as text 01002)")
        lines.append(f"  {full_xlsx}  (Excel, ZIP column typed as text)")

        # Test 1 report — ZIP / state problems only.
        zs_csv, zs_xlsx = _out(ZIP_STATE_PROBLEMS_CSV), _out(ZIP_STATE_PROBLEMS_XLSX)
        zip_state_problems.to_csv(zs_csv, index=False)
        _write_xlsx_zip_as_text(zip_state_problems, zs_xlsx)
        written += [zs_csv, zs_xlsx]
        lines.append("")
        lines.append(
            f"Saved ZIP/state problems ({len(zip_state_problems)} rows):"
        )
        lines.append(f"  {zs_csv}")
        lines.append(f"  {zs_xlsx}")

        # Test 2 report — federal district / state problems only.
        fd_csv = _out(FED_DISTRICT_PROBLEMS_CSV)
        fd_xlsx = _out(FED_DISTRICT_PROBLEMS_XLSX)
        fed_district_problems.to_csv(fd_csv, index=False)
        _write_xlsx_zip_as_text(fed_district_problems, fd_xlsx)
        written += [fd_csv, fd_xlsx]
        lines.append(
            f"Saved federal district problems ({len(fed_district_problems)} rows):"
        )
        lines.append(f"  {fd_csv}")
        lines.append(f"  {fd_xlsx}")

        # Optional: upload to S3. Objects are date-stamped (YYYYMMDD) so a
        # history accumulates in the bucket. Presigned URLs are still generated
        # and kept in the result for reference / the alternative link email.
        if S3_BUCKET:
            urls = upload_to_s3(written)
            result["presigned_urls"] = urls
            lines.append("")
            lines.append(
                f"Uploaded {len(urls)} dated file(s) to "
                f"s3://{S3_BUCKET}/{S3_PREFIX.strip('/')} (run date {RUN_DATE}):"
            )
            for url in urls:
                # Log the object name (before the query string), not the token.
                lines.append(f"  {url.split('?', 1)[0].rsplit('/', 1)[-1]}")

        # Summary counts for the returned result (handy for a Lambda response).
        result.update(
            status="OK",
            rows=int(len(df)),
            zip_state_problems=int(len(zip_state_problems)),
            federal_district_problems=int(len(fed_district_problems)),
            files=written,
        )

        # Optional: email the problem-report files as attachments (needs SES).
        # Independent of S3 — attachments come from the local written files.
        if SES_SENDER:
            emailed = notify_by_email(written, result)
            result["emailed_to"] = emailed
            if emailed:
                lines.append("")
                lines.append(f"Emailed report attachments to: {', '.join(emailed)}")
            else:
                lines.append("")
                lines.append("Email skipped (no recipients found).")

        lines.append("STATUS: OK")
    except Exception as exc:  # noqa: BLE001 - surface any error to the status file
        import traceback

        result["error"] = repr(exc)
        lines.append("STATUS: ERROR")
        lines.append(repr(exc))
        lines.append(traceback.format_exc())

    _write_status(lines)
    print("\n".join(lines))
    return result


def main() -> None:
    """Local entry point."""
    run()


def handler(event, context):  # noqa: ANN001 - Lambda signature
    """AWS Lambda entry point.

    Runs the same pipeline as `main()` but returns a JSON-serializable summary
    (status, counts, and presigned URLs) so the caller/logs can see the outcome.
    Set OUTPUT_DIR=/tmp on the function; /tmp is the only writable path in Lambda.
    """
    result = run()
    status_code = 200 if result.get("status") == "OK" else 500
    return {"statusCode": status_code, "result": result}


if __name__ == "__main__":
    main()
