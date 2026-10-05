"""Extract + filter the fields the ZIP/state + federal-district checks need.

Projects each NationBuilder V2 signup record down to:
    signup_id, registered_state, registered_zip, federal_district,
    us_citizen, date_last_verified

`us_citizen` and `date_last_verified` are NationBuilder **custom fields** (they
live in the signup's `custom_values`, not as top-level attributes), so the pull
must request `extra_fields[signups]=registered_address,custom_values`.

Filtering (applied downstream, before validation):
  - exclude where registered zip is blank/null
  - include only where us_citizen is truthy
  - exclude where date_last_verified is blank/null

All data is sourced from the NationBuilder V2 API (Insights is no longer used).
"""

from __future__ import annotations

from typing import Any, Generator

from nationbuilder_v2.client import NationBuilderV2Client

# Extra fields that must be requested from V2 to populate the projection.
EXTRA_FIELDS = ["registered_address", "custom_values"]

# Column order for the projected output.
COLUMNS = [
    "signup_id",
    "registered_state",
    "registered_zip",
    "federal_district",
    "us_citizen",
    "date_last_verified",
]


def _norm_zip(value: object) -> str:
    """Normalize a ZIP to its first five digits (handles ZIP+4 and blanks)."""
    if value is None:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:5] if digits else ""


def _is_citizen(value: object) -> bool:
    """True when the us_citizen custom value is affirmative.

    NationBuilder returns this custom field as a boolean (True/False), but we
    accept the common truthy encodings (1/"1"/"true"/"yes") defensively.
    """
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "t", "y"}


def _blank(value: object) -> bool:
    """True when a value is None or an empty/whitespace string."""
    return value is None or str(value).strip() == ""


def project_signup(record: dict[str, Any]) -> dict[str, Any]:
    """Project one V2 signup record to the fields we need.

    registered_state / registered_zip come from the sideloaded
    `registered_address`; federal_district is a top-level attribute;
    us_citizen and date_last_verified come from `custom_values`.
    """
    attrs = record.get("attributes", {}) or {}
    addr = attrs.get("registered_address") or {}
    custom = attrs.get("custom_values") or {}
    return {
        "signup_id": record.get("id"),
        "registered_state": (addr.get("state") or "").strip(),
        "registered_zip": _norm_zip(addr.get("zip")),
        "federal_district": (attrs.get("federal_district") or "").strip(),
        "us_citizen": custom.get("us_citizen"),
        "date_last_verified": custom.get("date_last_verified"),
    }


def keep_row(row: dict[str, Any]) -> bool:
    """Apply the inclusion filter to a projected row.

    Keep only rows that: have a non-blank registered zip, are us_citizen, and
    have a non-blank date_last_verified.
    """
    if _blank(row.get("registered_zip")):
        return False
    if not _is_citizen(row.get("us_citizen")):
        return False
    if _blank(row.get("date_last_verified")):
        return False
    return True


def exclusion_reason(row: dict[str, Any]) -> str | None:
    """Return why a row is excluded (for reporting), or None if it is kept."""
    if _blank(row.get("registered_zip")):
        return "zip blank"
    if not _is_citizen(row.get("us_citizen")):
        return "not us_citizen"
    if _blank(row.get("date_last_verified")):
        return "date_last_verified blank"
    return None


def extract_all_signups(
    client: NationBuilderV2Client | None = None,
    concurrency: int = 20,
    progress=None,
) -> list[dict[str, Any]]:
    """Pull ALL signups in parallel and project them (NO filtering applied).

    Builds a client from the environment if one is not supplied.
    """
    own_client = client is None
    client = client or NationBuilderV2Client.from_env()
    try:
        records = client.fetch_all_signups(
            extra_fields=EXTRA_FIELDS,
            concurrency=concurrency,
            progress=progress,
        )
        return [project_signup(r) for r in records]
    finally:
        if own_client:
            client.close()


def extract_filtered_signups(
    client: NationBuilderV2Client | None = None,
    concurrency: int = 20,
    progress=None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Pull all signups, project, and apply the inclusion filter.

    Returns (kept_rows, stats) where stats reports totals and the count of
    excluded rows by reason.
    """
    rows = extract_all_signups(
        client=client, concurrency=concurrency, progress=progress
    )
    kept: list[dict[str, Any]] = []
    stats = {
        "total": len(rows),
        "kept": 0,
        "excluded_zip_blank": 0,
        "excluded_not_us_citizen": 0,
        "excluded_date_last_verified_blank": 0,
    }
    reason_key = {
        "zip blank": "excluded_zip_blank",
        "not us_citizen": "excluded_not_us_citizen",
        "date_last_verified blank": "excluded_date_last_verified_blank",
    }
    for row in rows:
        reason = exclusion_reason(row)
        if reason is None:
            kept.append(row)
        else:
            stats[reason_key[reason]] += 1
    stats["kept"] = len(kept)
    return kept, stats


# -- Serial helpers (kept for small/sampling use) --------------------------


def iter_projected_signups(
    client: NationBuilderV2Client,
    page_size: int = 100,
    max_pages: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield projected rows serially (for sampling; prefer extract_* for full
    pulls)."""
    for record in client.iter_signups(
        extra_fields=EXTRA_FIELDS, page_size=page_size, max_pages=max_pages
    ):
        yield project_signup(record)
