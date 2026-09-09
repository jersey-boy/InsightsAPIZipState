"""
Original sample: list NationBuilder Insights data source fields.

This is the reference script the project started from. It has been updated to
load credentials from the environment (via nb_insights / .env) instead of
hardcoding a personal access token.
"""

from tableau_api_lib.utils.querying.datasources import get_all_datasource_fields

import warnings

warnings.filterwarnings("ignore")

from nb_insights import insights_connection


def main() -> None:
    with insights_connection() as conn:
        print("GET LIST OF INSIGHTS DATA SOURCE FIELDS")
        lst = get_all_datasource_fields(conn)
        for x in lst:
            for k, v in x.items():
                print(k, v)
    print("PROCESS END")


if __name__ == "__main__":
    main()
