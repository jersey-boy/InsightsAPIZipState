"""
Reusable connection helper for the NationBuilder Insights reporting system.

NationBuilder Insights is built on Tableau, so we connect through the Tableau
REST API using the `tableau_api_lib` library and a personal access token.

Credentials are loaded from a local `.env` file (see `.env.example`) so that
secrets never get hardcoded into source or committed to version control.

Typical usage:

    from nb_insights import insights_connection

    with insights_connection() as conn:
        # `conn` is a signed-in TableauServerConnection
        ...
    # sign-out happens automatically on exit
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
from tableau_api_lib import TableauServerConnection

# Load variables from `.env` into the environment (no-op if file is absent).
load_dotenv()


def _load_secret_into_env() -> None:
    """Populate NB_INSIGHTS_* env vars from AWS Secrets Manager, if configured.

    When NB_INSIGHTS_SECRET_ID is set (a secret name or ARN), the secret's JSON
    value is read and each key copied into os.environ *without* overwriting an
    existing variable. This means local `.env`/shell values still win, and in
    Lambda the secret supplies the credentials — especially the token secret,
    which should not live in plain function env vars.

    A no-op when NB_INSIGHTS_SECRET_ID is unset, so local runs are unaffected.
    """
    secret_id = os.getenv("NB_INSIGHTS_SECRET_ID")
    if not secret_id:
        return

    import boto3  # lazy; only needed when a secret is configured

    region = os.getenv("NB_INSIGHTS_SECRET_REGION") or os.getenv("AWS_REGION")
    client = boto3.client("secretsmanager", region_name=region or None)
    raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
    for key, value in json.loads(raw).items():
        os.environ.setdefault(key, str(value))


_load_secret_into_env()


class MissingCredentialsError(RuntimeError):
    """Raised when required environment variables are not set."""


def _require(name: str) -> str:
    """Return the value of an env var, or raise a clear error if it's missing."""
    value = os.getenv(name)
    if not value:
        raise MissingCredentialsError(
            f"Environment variable '{name}' is not set. "
            f"Copy .env.example to .env and fill in your credentials."
        )
    return value


def build_config() -> dict:
    """Build the tableau_api_lib config dict from environment variables."""
    return {
        "tableau_api": {
            "server": os.getenv(
                "NB_INSIGHTS_SERVER", "https://login.insights.nationbuilder.com"
            ),
            "api_version": os.getenv("NB_INSIGHTS_API_VERSION", "3.21"),
            "personal_access_token_name": _require("NB_INSIGHTS_TOKEN_NAME"),
            "personal_access_token_secret": _require("NB_INSIGHTS_TOKEN_SECRET"),
            "site_name": _require("NB_INSIGHTS_SITE_NAME"),
            "site_url": _require("NB_INSIGHTS_SITE_URL"),
        }
    }


def connect() -> TableauServerConnection:
    """Create a TableauServerConnection and sign in. Caller must sign out."""
    conn = TableauServerConnection(config_json=build_config(), env="tableau_api")
    conn.sign_in()
    return conn


@contextmanager
def insights_connection() -> Iterator[TableauServerConnection]:
    """Context manager that signs in, yields the connection, and signs out."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.sign_out()


if __name__ == "__main__":
    # Quick smoke test: sign in, report success, sign out.
    with insights_connection() as conn:
        print("Signed in to NationBuilder Insights successfully.")
        print(f"Server:  {conn.server}")
        print(f"Site:    {conn.site_url}")
    print("Signed out.")
