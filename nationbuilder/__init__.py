"""NationBuilder V1 API Python Client.

Vendored from the NBGet01 project. Provides a thin typed client over the
NationBuilder V1 REST API, used here to cross-check the federal_district value
for Insights-flagged records.
"""

from nationbuilder.client import NationBuilderClient
from nationbuilder.models import Address, PaginatedResponse, Person
from nationbuilder.exceptions import (
    AuthenticationError,
    ForbiddenError,
    NationBuilderError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

__all__ = [
    "NationBuilderClient",
    "Address",
    "PaginatedResponse",
    "Person",
    "AuthenticationError",
    "ForbiddenError",
    "NationBuilderError",
    "NotFoundError",
    "RateLimitError",
    "ValidationError",
]
