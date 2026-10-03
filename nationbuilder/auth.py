"""OAuth 2.0 authentication helpers for NationBuilder API."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


@dataclass
class OAuthConfig:
    """OAuth 2.0 configuration for NationBuilder."""

    client_id: str
    client_secret: str
    redirect_uri: str
    nation_slug: str

    @property
    def base_url(self) -> str:
        return f"https://{self.nation_slug}.nationbuilder.com"

    def get_authorize_url(self, response_type: str = "code") -> str:
        """Build the OAuth authorization URL to redirect the user to.

        Args:
            response_type: OAuth response type (default: "code").

        Returns:
            The full authorization URL.
        """
        params = {
            "response_type": response_type,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
        }
        return f"{self.base_url}/oauth/authorize?{urlencode(params)}"

    def exchange_code_for_token(self, code: str) -> TokenResponse:
        """Exchange an authorization code for an access token.

        Args:
            code: The authorization code from the OAuth callback.

        Returns:
            A TokenResponse containing access and refresh tokens.
        """
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
        }
        response = httpx.post(f"{self.base_url}/oauth/token", json=payload)
        response.raise_for_status()
        data = response.json()
        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in"),
        )

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Refresh an expired access token using a refresh token.

        Args:
            refresh_token: The refresh token from a previous token exchange.

        Returns:
            A new TokenResponse with fresh access and refresh tokens.
        """
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        response = httpx.post(f"{self.base_url}/oauth/token", json=payload)
        response.raise_for_status()
        data = response.json()
        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in"),
        )


@dataclass
class TokenResponse:
    """Represents the response from a token exchange or refresh."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int | None = None
