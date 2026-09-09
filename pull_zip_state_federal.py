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
OUTPUT_CSV = "zip_state_federal_district.csv"
PROBLEMS_CSV = "zip_state_federal_district_problems.csv"
ZIP_STATE_LOOKUP = "zip_state_lookup.csv"

pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)


STATUS_FILE = "pull_zip_state_federal.status.txt"


def _write_status(lines: list[str]) -> None:
    """Write status/preview to a workspace file (terminal capture is flaky here)."""
    with open(STATUS_FILE, "w") as fh:
        fh.write("\n".join(lines) + "\n")


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
        # Build a zip -> state lookup from the reference file. Zips are matched
        # as integers on both sides, which keeps leading-zero zips consistent
        # (the view stores registered_zip_clean as a number, e.g. 1002).
        lookup = pd.read_csv(ZIP_STATE_LOOKUP)
        zip_to_state = dict(
            zip(
                pd.to_numeric(lookup["zip"], errors="coerce"),
                lookup["state"].astype(str).str.strip().str.upper(),
            )
        )

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

        lines.append(f"View: {VIEW_NAME}  (id={VIEW_ID})")
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

        df.to_csv(OUTPUT_CSV, index=False)
        lines.append("")
        lines.append(f"Saved full data to {OUTPUT_CSV} ({len(df)} rows).")

        # Second file: only the problem transactions. A row is a problem if
        # either check flagged it (i.e. its problem field is non-blank).
        problems = df[
            (df["zip_state_problem"] != "")
            | (df["federal_district_problem"] != "")
        ]
        problems.to_csv(PROBLEMS_CSV, index=False)
        lines.append(f"Saved problem rows to {PROBLEMS_CSV} ({len(problems)} rows).")
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
