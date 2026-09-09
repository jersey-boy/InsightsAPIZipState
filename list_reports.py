"""
List the reports available in the NationBuilder Insights reporting system.

Insights is built on Tableau, so "reports" map to Tableau content:
  * Workbooks   - a report file; the top-level container users open
  * Views       - the individual sheets/dashboards inside each workbook
  * Data sources - the published datasets that workbooks draw from

This app signs in (using credentials from .env via nb_insights), pulls each
of these as a pandas DataFrame, prints a readable summary to the console, and
writes a CSV for each so you can inspect the full detail.

Run:
    .venv/bin/python list_reports.py
"""

from __future__ import annotations

import pandas as pd
from tableau_api_lib.utils.querying import (
    get_workbooks_dataframe,
    get_views_dataframe,
    get_datasources_dataframe,
)

from nb_insights import insights_connection

# Show all rows/columns when printing to the console.
pd.set_option("display.max_rows", None)
pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)


def _first_present(df: pd.DataFrame, candidates: list[str]) -> list[str]:
    """Return the subset of candidate column names that exist in df, in order."""
    return [c for c in candidates if c in df.columns]


def print_summary(title: str, df: pd.DataFrame, columns: list[str]) -> None:
    """Print a titled, trimmed table of the most useful columns."""
    print(f"\n{'=' * 70}\n{title}  ({len(df)} found)\n{'=' * 70}")
    if df.empty:
        print("  (none)")
        return
    cols = _first_present(df, columns)
    view = df[cols] if cols else df
    print(view.to_string(index=False))


def main() -> None:
    with insights_connection() as conn:
        workbooks_df = get_workbooks_dataframe(conn)
        views_df = get_views_dataframe(conn)
        datasources_df = get_datasources_dataframe(conn)

    # ---- Console summaries -------------------------------------------------
    print_summary(
        "WORKBOOKS (reports)",
        workbooks_df,
        ["name", "contentUrl", "createdAt", "updatedAt", "id"],
    )
    print_summary(
        "VIEWS (sheets & dashboards inside reports)",
        views_df,
        ["name", "contentUrl", "viewUrlName", "id"],
    )
    print_summary(
        "DATA SOURCES (datasets powering the reports)",
        datasources_df,
        ["name", "type", "contentUrl", "updatedAt", "id"],
    )

    # ---- CSV output --------------------------------------------------------
    workbooks_df.to_csv("reports_workbooks.csv", index=False)
    views_df.to_csv("reports_views.csv", index=False)
    datasources_df.to_csv("reports_datasources.csv", index=False)

    print(f"\n{'=' * 70}")
    print("Saved:")
    print(f"  reports_workbooks.csv    ({len(workbooks_df)} rows)")
    print(f"  reports_views.csv        ({len(views_df)} rows)")
    print(f"  reports_datasources.csv  ({len(datasources_df)} rows)")


if __name__ == "__main__":
    main()
