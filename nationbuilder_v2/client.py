"""NationBuilder V2 API client with OAuth 2.0 refresh-token flow.

Usage:
    from nationbuilder_v2 import NationBuilderV2Client

    client = NationBuilderV2Client.from_env()   # refreshes the access token
    for signup in client.iter_signups(extra_fields=["registered_address"]):
        ...

Design (see V2_OAUTH_SETUP.md):
- V2 access tokens expire in 24h, so we refresh at construction using the
  stored refresh token ("just-in-time" — always start a run with a valid token).
- NationBuilder rotates the refresh token on each refresh, so the new one is
  persisted via `on_refresh` (defaults to rewriting the local .env).
- Requests use `Authorization: Bearer <access_token>` against /api/v2.
- Pagination follows the `links.next` URL until it is absent.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Generator, Iterable

import httpx

from nationbuilder_v2.exceptions import NationBuilderV2Error


def _persist_refresh_token_to_env(refresh_token: str, env_path: str = ".env") -> None:
    """Write a rotated refresh token back into the local .env (best effort)."""
    if not os.path.exists(env_path):
        return
    text = open(env_path, encoding="utf-8").read()
    key = "NATIONBUILDER_REFRESH_TOKEN"
    line = f"{key}={refresh_token}"
    if re.search(rf"(?m)^{key}=.*$", text):
        text = re.sub(rf"(?m)^{key}=.*$", line, text)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    open(env_path, "w", encoding="utf-8", newline="\n").write(text)


class NationBuilderV2Client:
    """Client for the NationBuilder V2 API."""

    def __init__(
        self,
        slug: str,
        client_id: str,
        client_secret: str,
        refresh_token: str | None = None,
        access_token: str | None = None,
        on_refresh: Callable[[str], None] | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Create a client.

        If a refresh_token is provided, an access token is minted immediately
        (recommended). Otherwise a pre-obtained access_token must be supplied.

        Args:
            slug: Nation slug (subdomain).
            client_id / client_secret: OAuth app credentials.
            refresh_token: OAuth refresh token (durable); used to mint access
                tokens and rotated on each refresh.
            access_token: Optional pre-obtained access token (used only when no
                refresh_token is given).
            on_refresh: Callback invoked with the newly rotated refresh token so
                the caller can persist it. Defaults to rewriting the local .env.
            timeout: HTTP timeout in seconds.
        """
        self.slug = slug
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.on_refresh = on_refresh or _persist_refresh_token_to_env
        self.base_url = f"https://{slug}.nationbuilder.com/api/v2"
        self.oauth_token_url = f"https://{slug}.nationbuilder.com/oauth/token"
        self._http = httpx.Client(timeout=timeout)

        if self.refresh_token:
            self.refresh_access_token()
        elif not self.access_token:
            raise NationBuilderV2Error(
                "Provide either a refresh_token or an access_token."
            )

    @classmethod
    def from_env(
        cls,
        timeout: float = 30.0,
        on_refresh: Callable[[str], None] | None = None,
    ) -> "NationBuilderV2Client":
        """Build a client from the NATIONBUILDER_* environment variables.

        Requires NATIONBUILDER_SLUG, NATIONBUILDER_CLIENT_ID,
        NATIONBUILDER_CLIENT_SECRET, and NATIONBUILDER_REFRESH_TOKEN. Falls back
        to NATIONBUILDER_ACCESS_TOKEN_V2 if no refresh token is set.

        `on_refresh` persists the rotated refresh token; pass a Secrets-Manager
        writer in Lambda. Defaults to rewriting the local .env.
        """

        def _req(name: str) -> str:
            val = os.getenv(name)
            if not val:
                raise NationBuilderV2Error(f"Missing env var: {name}")
            return val

        return cls(
            slug=_req("NATIONBUILDER_SLUG"),
            client_id=_req("NATIONBUILDER_CLIENT_ID"),
            client_secret=_req("NATIONBUILDER_CLIENT_SECRET"),
            refresh_token=os.getenv("NATIONBUILDER_REFRESH_TOKEN"),
            access_token=os.getenv("NATIONBUILDER_ACCESS_TOKEN_V2"),
            on_refresh=on_refresh,
            timeout=timeout,
        )

    # -- OAuth -----------------------------------------------------------------

    def refresh_access_token(self) -> None:
        """Exchange the refresh token for a fresh access token.

        Updates self.access_token and self.refresh_token (rotated), and calls
        on_refresh so the rotated refresh token can be persisted.
        """
        if not self.refresh_token:
            raise NationBuilderV2Error("No refresh_token to refresh with.")
        resp = self._http.post(
            self.oauth_token_url,
            json={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        if resp.status_code != 200:
            raise NationBuilderV2Error(
                "Token refresh failed",
                status_code=resp.status_code,
                endpoint="/oauth/token",
                response_body=resp.text,
            )
        data = resp.json()
        self.access_token = data["access_token"]
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self.refresh_token:
            self.refresh_token = new_refresh
            try:
                self.on_refresh(new_refresh)
            except Exception:  # noqa: BLE001 - persistence is best-effort
                pass

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    # -- Requests --------------------------------------------------------------

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict:
        """GET a V2 endpoint. `path_or_url` may be:
          - a full URL (``http...``),
          - a root-relative path already including the API prefix
            (``/api/v2/signups?...``, as returned in links.next), or
          - a short path relative to the API base (``/signups``).
        Returns parsed JSON.
        """
        if path_or_url.startswith("http"):
            url = path_or_url
        elif path_or_url.startswith("/api/"):
            # links.next is root-relative and already includes /api/v2.
            url = f"https://{self.slug}.nationbuilder.com{path_or_url}"
        else:
            url = f"{self.base_url}{path_or_url}"
        resp = self._http.get(url, headers=self._headers(), params=params)
        if resp.status_code != 200:
            raise NationBuilderV2Error(
                "API request failed",
                status_code=resp.status_code,
                endpoint=url,
                response_body=resp.text,
            )
        return resp.json()

    # -- Signups ---------------------------------------------------------------

    def iter_signups(
        self,
        extra_fields: Iterable[str] | None = None,
        page_size: int = 100,
        max_pages: int | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield each signup's `data` record across all pages, serially.

        Simple sequential pagination (follows links.next). For the full ~188k
        pull use `fetch_all_signups()`, which is far faster via parallelism.

        Args:
            extra_fields: names to request via extra_fields[signups]=...
                (e.g. ["registered_address"]).
            page_size: page[size] (NationBuilder caps this at 100).
            max_pages: optional cap on pages fetched (for testing / sampling).

        Yields:
            Raw JSON:API signup records: {"type","id","attributes":{...}}.
        """
        params: dict[str, Any] = {"page[size]": str(page_size)}
        if extra_fields:
            params["extra_fields[signups]"] = ",".join(extra_fields)

        data = self.get("/signups", params=params)
        pages = 0
        while True:
            for record in data.get("data", []):
                yield record
            pages += 1
            if max_pages is not None and pages >= max_pages:
                break
            next_url = (data.get("links") or {}).get("next")
            if not next_url:
                break
            data = self.get(next_url)

    def count_signups(self) -> int:
        """Return the total number of signups via stats[total]=count.

        Note: the stats feature has proven intermittent on this endpoint, so
        callers should not depend on a correct value — `fetch_all_signups`
        detects the end of data by empty pages instead.
        """
        data = self.get("/signups", params={"stats[total]": "count", "page[size]": "1"})
        return int((data.get("stats") or {}).get("total", {}).get("count", 0))

    def _get_page(
        self, page_number: int, params: dict[str, Any], max_retries: int = 4
    ) -> list[dict[str, Any]]:
        """Fetch a single page by number, retrying on 429/5xx with backoff."""
        import random
        import time

        page_params = dict(params)
        page_params["page[number]"] = str(page_number)
        for attempt in range(max_retries + 1):
            resp = self._http.get(
                f"{self.base_url}/signups",
                headers=self._headers(),
                params=page_params,
            )
            if resp.status_code == 200:
                return resp.json().get("data", [])
            # Retry on rate-limit and transient server errors.
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else (2**attempt) * 0.5
                time.sleep(wait + random.uniform(0, 0.25))
                continue
            raise NationBuilderV2Error(
                "Page fetch failed",
                status_code=resp.status_code,
                endpoint=f"/signups?page[number]={page_number}",
                response_body=resp.text,
            )
        return []

    def fetch_all_signups(
        self,
        extra_fields: Iterable[str] | None = None,
        page_size: int = 100,
        concurrency: int = 20,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch every signup record in parallel (page-number pagination).

        Pulls the total count first, computes the page count, then fetches all
        pages concurrently with a bounded thread pool. NationBuilder's V2 uses
        page-number pagination, so any page can be requested directly — this is
        what makes parallelism possible. Each page retries on 429/5xx.

        Args:
            extra_fields: e.g. ["registered_address"].
            page_size: capped at 100 by the API.
            concurrency: worker threads (20 is a safe, fast default here).
            progress: optional callback(pages_done, pages_total).

        Returns:
            All signup `data` records (order not guaranteed).
        """
        from concurrent.futures import ThreadPoolExecutor

        params: dict[str, Any] = {"page[size]": str(min(page_size, 100))}
        if extra_fields:
            params["extra_fields[signups]"] = ",".join(extra_fields)

        # The stats count is unreliable here, so we don't trust it to know the
        # page count. Instead we fetch pages in parallel BATCHES and stop once a
        # whole batch comes back empty (i.e. we've run off the end of the data).
        records: list[dict[str, Any]] = []
        next_page = 1
        batch = max(1, concurrency)
        done = 0
        with ThreadPoolExecutor(max_workers=batch) as pool:
            while True:
                page_numbers = list(range(next_page, next_page + batch))
                results = list(pool.map(lambda pn: self._get_page(pn, params), page_numbers))
                got_any = False
                for data in results:
                    if data:
                        records.extend(data)
                        got_any = True
                done += batch
                if progress:
                    progress(done, 0)  # total unknown; report pages attempted
                # Stop when the entire batch was empty — end of data.
                if not got_any:
                    break
                next_page += batch
        return records

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "NationBuilderV2Client":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
