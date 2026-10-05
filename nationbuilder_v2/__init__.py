"""NationBuilder V2 API client (OAuth 2.0).

The V2 API is the sole data source. This package replaced the legacy V1
NationBuilder client and the Insights/Tableau code, which have been removed.

See V2_OAUTH_SETUP.md for the OAuth flow and the confirmed signups schema.
"""

from nationbuilder_v2.client import NationBuilderV2Client
from nationbuilder_v2.exceptions import NationBuilderV2Error

__all__ = ["NationBuilderV2Client", "NationBuilderV2Error"]
