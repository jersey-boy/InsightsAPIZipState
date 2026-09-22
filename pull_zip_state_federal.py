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

import pandas as pd
from tableau_api_lib.utils.querying import get_view_data_dataframe

from nb_insights import insights_connection

# The "Zip State Federal District" view (from reports_views.csv).
VIEW_ID = "c7f541b2-87db-4fad-97c5-2e532dbbe956"
VIEW_NAME = "Zip State Federal District"

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


def _write_status(lines: list[str]) -> None:
    """Write status/preview to a workspace file (terminal capture is flaky here)."""
    with open(STATUS_FILE, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def build_zip_to_state(mode: str = ZIP_MODE) -> dict[int, str]:
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
            ZIP_STATE_LOOKUP, sheet_name=sheet, header=cols["header"]
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


def main() -> None:
    lines: list[str] = []
    try:
        with insights_connection() as conn:
            df = get_view_data_dataframe(conn, view_id=VIEW_ID)

        # Reorder columns for the output CSV. Any columns present in the data
        # but not listed here are appended afterwards so nothing is dropped.
        column_order = [
            "signup_id",
            "full_name",
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
        zip_to_state = build_zip_to_state(ZIP_MODE)

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

        # Full dataset: universal CSV + Excel-friendly XLSX (keeps both flags).
        df.to_csv(OUTPUT_CSV, index=False)
        _write_xlsx_zip_as_text(df, OUTPUT_XLSX)
        lines.append("")
        lines.append(f"Saved full data ({len(df)} rows):")
        lines.append(f"  {OUTPUT_CSV}   (universal, ZIP as text 01002)")
        lines.append(f"  {OUTPUT_XLSX}  (Excel, ZIP column typed as text)")

        # Test 1 report — ZIP / state problems only.
        zip_state_problems.to_csv(ZIP_STATE_PROBLEMS_CSV, index=False)
        _write_xlsx_zip_as_text(zip_state_problems, ZIP_STATE_PROBLEMS_XLSX)
        lines.append("")
        lines.append(
            f"Saved ZIP/state problems ({len(zip_state_problems)} rows):"
        )
        lines.append(f"  {ZIP_STATE_PROBLEMS_CSV}")
        lines.append(f"  {ZIP_STATE_PROBLEMS_XLSX}")

        # Test 2 report — federal district / state problems only.
        fed_district_problems.to_csv(FED_DISTRICT_PROBLEMS_CSV, index=False)
        _write_xlsx_zip_as_text(fed_district_problems, FED_DISTRICT_PROBLEMS_XLSX)
        lines.append(
            f"Saved federal district problems ({len(fed_district_problems)} rows):"
        )
        lines.append(f"  {FED_DISTRICT_PROBLEMS_CSV}")
        lines.append(f"  {FED_DISTRICT_PROBLEMS_XLSX}")
        lines.append("STATUS: OK")
    except Exception as exc:  # noqa: BLE001 - surface any error to the status file
        import traceback

        lines.append("STATUS: ERROR")
        lines.append(repr(exc))
        lines.append(traceback.format_exc())

    _write_status(lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
