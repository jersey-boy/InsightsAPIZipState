"""Exceptions for the NationBuilder V2 client."""

from __future__ import annotations

from typing import Any


class NationBuilderV2Error(Exception):
    """Raised when a V2 API or OAuth request fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        endpoint: str | None = None,
        response_body: Any = None,
    ) -> None:
        self.status_code = status_code
        self.endpoint = endpoint
        self.response_body = response_body
        detail = f"[{status_code}] {endpoint}: {message}" if status_code else message
        super().__init__(detail)
