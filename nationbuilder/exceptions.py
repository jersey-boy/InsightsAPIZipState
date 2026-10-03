"""Custom exceptions for the NationBuilder API client."""

from __future__ import annotations

from typing import Any


class NationBuilderError(Exception):
    """Base exception for NationBuilder API errors."""

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


class AuthenticationError(NationBuilderError):
    """Raised when authentication fails (401)."""

    pass


class ForbiddenError(NationBuilderError):
    """Raised when access is denied (403)."""

    pass


class NotFoundError(NationBuilderError):
    """Raised when a resource is not found (404)."""

    pass


class ValidationError(NationBuilderError):
    """Raised when a request body fails validation (422)."""

    pass


class RateLimitError(NationBuilderError):
    """Raised when the API rate limit is hit (429)."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message, **kwargs)
