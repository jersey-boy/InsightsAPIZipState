"""Main NationBuilder V1 API client."""

from __future__ import annotations

import time
from typing import Any, Generator

import httpx

from nationbuilder.exceptions import (
    AuthenticationError,
    ForbiddenError,
    NationBuilderError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from nationbuilder.models import PaginatedResponse, Person


class NationBuilderClient:
    """Client for interacting with the NationBuilder V1 API.

    Usage:
        client = NationBuilderClient(slug="your_nation", access_token="your_token")
        people = client.list_people()
    """

    def __init__(
        self,
        slug: str,
        access_token: str,
        max_retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the NationBuilder V1 API client.

        Args:
            slug: Your nation's slug (subdomain).
            access_token: A valid V1 API access token.
            max_retries: Max retries on rate-limit (429) responses.
            timeout: HTTP request timeout in seconds.
        """
        self.slug = slug
        self.access_token = access_token
        self.max_retries = max_retries
        self.base_url = f"https://{slug}.nationbuilder.com/api/v1"
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def _default_params(self) -> dict[str, str]:
        """Return default query params including the access token."""
        return {"access_token": self.access_token}

    def _handle_response(self, response: httpx.Response, endpoint: str) -> Any:
        """Process an API response, raising appropriate exceptions on error.

        Args:
            response: The httpx response object.
            endpoint: The endpoint path for error messages.

        Returns:
            Parsed JSON response body, or None for 204 responses.

        Raises:
            AuthenticationError: On 401 responses.
            ForbiddenError: On 403 responses.
            NotFoundError: On 404 responses.
            ValidationError: On 422 responses.
            RateLimitError: On 429 responses.
            NationBuilderError: On other error responses.
        """
        if response.status_code == 204:
            return None

        if response.status_code in (200, 201):
            return response.json()

        body = response.text
        status = response.status_code

        if status == 401:
            raise AuthenticationError(
                "Invalid or expired access token",
                status_code=status,
                endpoint=endpoint,
                response_body=body,
            )
        elif status == 403:
            raise ForbiddenError(
                "Access denied - insufficient permissions",
                status_code=status,
                endpoint=endpoint,
                response_body=body,
            )
        elif status == 404:
            raise NotFoundError(
                "Resource not found",
                status_code=status,
                endpoint=endpoint,
                response_body=body,
            )
        elif status == 422:
            raise ValidationError(
                f"Validation failed: {body}",
                status_code=status,
                endpoint=endpoint,
                response_body=body,
            )
        elif status == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                retry_after=float(retry_after) if retry_after else None,
                status_code=status,
                endpoint=endpoint,
                response_body=body,
            )
        else:
            raise NationBuilderError(
                f"API request failed: {body}",
                status_code=status,
                endpoint=endpoint,
                response_body=body,
            )

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Make an HTTP request with retry logic for rate limiting.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint path (e.g., "/people").
            params: Optional query parameters (access_token is added automatically).
            json_body: Optional JSON request body.

        Returns:
            Parsed JSON response.
        """
        request_params = self._default_params()
        if params:
            request_params.update(params)

        for attempt in range(self.max_retries + 1):
            response = self._http.request(
                method=method,
                url=endpoint,
                params=request_params,
                json=json_body,
            )

            if response.status_code == 429:
                if attempt < self.max_retries:
                    retry_after = response.headers.get("Retry-After", "1")
                    wait_time = float(retry_after) if retry_after else 2**attempt
                    time.sleep(wait_time)
                    continue
                self._handle_response(response, endpoint)

            return self._handle_response(response, endpoint)

    # -------------------------------------------------------------------------
    # People
    # -------------------------------------------------------------------------

    def list_people(self, limit: int = 10) -> PaginatedResponse:
        """List people with pagination.

        Args:
            limit: Number of results per page (max 100, default 10).

        Returns:
            A PaginatedResponse containing people data.
        """
        params = {"limit": str(min(limit, 100))}
        data = self._request("GET", "/people", params=params)
        return PaginatedResponse.from_v1_response(data)

    def get_person(self, person_id: int | str) -> Person:
        """Get a single person by NationBuilder ID.

        Args:
            person_id: The NationBuilder ID of the person.

        Returns:
            A Person instance.
        """
        data = self._request("GET", f"/people/{person_id}")
        return Person.from_dict(data["person"])

    def get_person_by_external_id(self, external_id: str) -> Person:
        """Get a person by their external ID.

        Args:
            external_id: The external ID of the person.

        Returns:
            A Person instance.
        """
        data = self._request(
            "GET", f"/people/{external_id}", params={"id_type": "external"}
        )
        return Person.from_dict(data["person"])

    def create_person(self, person: Person) -> Person:
        """Create a new person.

        A person is valid with a name, phone number, or email.

        Args:
            person: A Person instance with desired attributes set.

        Returns:
            The created Person with ID populated.
        """
        payload = {"person": person.to_dict()}
        data = self._request("POST", "/people", json_body=payload)
        return Person.from_dict(data["person"])

    def update_person(self, person_id: int | str, person: Person) -> Person:
        """Update an existing person.

        Args:
            person_id: The NationBuilder ID of the person to update.
            person: A Person instance with the updated attributes.

        Returns:
            The updated Person.
        """
        payload = {"person": person.to_dict()}
        data = self._request("PUT", f"/people/{person_id}", json_body=payload)
        return Person.from_dict(data["person"])

    def push_person(self, person: Person) -> tuple[Person, bool]:
        """Create or update a person using match (push endpoint).

        Attempts to match on email, external_id, facebook_username, twitter_login,
        or other unique identifiers. If matched, updates; if not, creates.

        Args:
            person: A Person instance with attributes including a matchable ID.

        Returns:
            A tuple of (Person, was_created) where was_created is True if new.
        """
        payload = {"person": person.to_dict()}
        response = self._http.request(
            method="PUT",
            url="/people/push",
            params=self._default_params(),
            json=payload,
        )
        result = self._handle_response(response, "/people/push")
        was_created = response.status_code == 201
        return Person.from_dict(result["person"]), was_created

    def delete_person(self, person_id: int | str) -> None:
        """Delete a person by ID.

        Args:
            person_id: The NationBuilder ID of the person to delete.
        """
        self._request("DELETE", f"/people/{person_id}")

    def count_people(self) -> int:
        """Get the total count of people in the nation.

        Returns:
            Total number of people.
        """
        data = self._request("GET", "/people/count")
        return data["people_count"]

    def me(self) -> Person:
        """Get the person associated with the current access token.

        Returns:
            A Person instance representing the token owner.
        """
        data = self._request("GET", "/people/me")
        return Person.from_dict(data["person"])

    # -------------------------------------------------------------------------
    # People Search
    # -------------------------------------------------------------------------

    def search_people(
        self,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        city: str | None = None,
        state: str | None = None,
        sex: str | None = None,
        birthdate: str | None = None,
        updated_since: str | None = None,
        with_mobile: bool | None = None,
        custom_values: dict[str, str] | None = None,
        limit: int = 10,
    ) -> PaginatedResponse:
        """Search for people matching given criteria.

        Args:
            first_name: Filter by first name.
            last_name: Filter by last name.
            email: Filter by email address.
            city: Filter by city of primary address.
            state: Filter by state of primary address.
            sex: Filter by sex (M or F).
            birthdate: Filter by birthdate.
            updated_since: Filter by updated since date.
            with_mobile: Only people with mobile numbers.
            custom_values: Match custom field values, e.g. {"field_slug": "value"}.
            limit: Results per page (max 100).

        Returns:
            A PaginatedResponse with matching people.
        """
        params: dict[str, Any] = {"limit": str(min(limit, 100))}
        if first_name:
            params["first_name"] = first_name
        if last_name:
            params["last_name"] = last_name
        if email:
            params["email"] = email
        if city:
            params["city"] = city
        if state:
            params["state"] = state
        if sex:
            params["sex"] = sex
        if birthdate:
            params["birthdate"] = birthdate
        if updated_since:
            params["updated_since"] = updated_since
        if with_mobile is not None:
            params["with_mobile"] = "true" if with_mobile else "false"
        if custom_values:
            for key, val in custom_values.items():
                params[f"custom_values[{key}]"] = val

        data = self._request("GET", "/people/search", params=params)
        return PaginatedResponse.from_v1_response(data)

    def match_person(
        self,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
    ) -> Person:
        """Find a single person matching the given criteria.

        Args:
            email: Email address to match.
            first_name: First name to match.
            last_name: Last name to match.
            phone: Phone number to match.
            mobile: Mobile number to match.

        Returns:
            A matched Person.

        Raises:
            NotFoundError: If no single match is found.
        """
        params: dict[str, str] = {}
        if email:
            params["email"] = email
        if first_name:
            params["first_name"] = first_name
        if last_name:
            params["last_name"] = last_name
        if phone:
            params["phone"] = phone
        if mobile:
            params["mobile"] = mobile

        data = self._request("GET", "/people/match", params=params)
        return Person.from_dict(data["person"])

    def nearby_people(
        self,
        latitude: float,
        longitude: float,
        distance: float = 1.0,
        limit: int = 10,
    ) -> PaginatedResponse:
        """Search for people near a geographic location.

        Args:
            latitude: Latitude of search origin.
            longitude: Longitude of search origin.
            distance: Radius in miles (default 1).
            limit: Results per page (max 100).

        Returns:
            A PaginatedResponse with nearby people.
        """
        params: dict[str, str] = {
            "location": f"{latitude},{longitude}",
            "distance": str(distance),
            "limit": str(min(limit, 100)),
        }
        data = self._request("GET", "/people/nearby", params=params)
        return PaginatedResponse.from_v1_response(data)

    # -------------------------------------------------------------------------
    # Tags
    # -------------------------------------------------------------------------

    def get_person_tags(self, person_id: int | str) -> list[str]:
        """Get all tags for a person.

        Args:
            person_id: The NationBuilder ID of the person.

        Returns:
            A list of tag strings.
        """
        data = self._request("GET", f"/people/{person_id}/taggings")
        taggings = data.get("taggings", [])
        return [t["tag"] for t in taggings]

    def tag_person(self, person_id: int | str, tags: str | list[str]) -> None:
        """Apply one or more tags to a person.

        Args:
            person_id: The NationBuilder ID of the person.
            tags: A single tag string or list of tag strings.
        """
        payload = {"tagging": {"tag": tags}}
        self._request("PUT", f"/people/{person_id}/taggings", json_body=payload)

    def untag_person(self, person_id: int | str, tag: str) -> None:
        """Remove a single tag from a person.

        Args:
            person_id: The NationBuilder ID of the person.
            tag: The tag to remove.
        """
        self._request("DELETE", f"/people/{person_id}/taggings/{tag}")

    def bulk_untag_person(self, person_id: int | str, tags: str | list[str]) -> None:
        """Remove one or more tags from a person.

        Args:
            person_id: The NationBuilder ID of the person.
            tags: A single tag string or list of tag strings to remove.
        """
        payload = {"tagging": {"tag": tags}}
        # V1 API uses DELETE with a body for batch tag removal
        response = self._http.request(
            method="DELETE",
            url=f"/people/{person_id}/taggings",
            params=self._default_params(),
            json=payload,
        )
        self._handle_response(response, f"/people/{person_id}/taggings")

    # -------------------------------------------------------------------------
    # Pagination helpers
    # -------------------------------------------------------------------------

    def get_next_page(self, paginated_response: PaginatedResponse) -> PaginatedResponse | None:
        """Fetch the next page from a paginated response.

        Args:
            paginated_response: A previous PaginatedResponse with a next URL.

        Returns:
            The next PaginatedResponse, or None if no more pages.
        """
        if not paginated_response.next_url:
            return None

        # The next URL from V1 is a path from root, e.g. /api/v1/people?__nonce=xxx&__token=yyy
        # Build the full absolute URL since our httpx client base_url already includes /api/v1
        base = f"https://{self.slug}.nationbuilder.com"
        full_url = f"{base}{paginated_response.next_url}"
        separator = "&" if "?" in full_url else "?"
        full_url = f"{full_url}{separator}access_token={self.access_token}"

        response = httpx.get(
            full_url,
            headers={"Accept": "application/json"},
            timeout=self._http.timeout,
        )
        data = self._handle_response(response, paginated_response.next_url)
        return PaginatedResponse.from_v1_response(data)

    def iter_all_people(self, limit: int = 100) -> Generator[Person, None, None]:
        """Iterate through all people across all pages.

        Yields Person objects one at a time, automatically handling pagination.

        Args:
            limit: Number of results per page (max 100).

        Yields:
            Person instances.
        """
        response = self.list_people(limit=limit)
        while True:
            for person_data in response.results:
                yield Person.from_dict(person_data)
            if not response.next_url:
                break
            next_page = self.get_next_page(response)
            if next_page is None or not next_page.results:
                break
            response = next_page

    # -------------------------------------------------------------------------
    # Generic / Raw
    # -------------------------------------------------------------------------

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a raw GET request to any V1 endpoint.

        Args:
            endpoint: The endpoint path (e.g., "/people" or "/donations").
            params: Optional query parameters.

        Returns:
            Parsed JSON response.
        """
        return self._request("GET", endpoint, params=params)

    def post(self, endpoint: str, json_body: dict[str, Any] | None = None) -> Any:
        """Make a raw POST request to any V1 endpoint.

        Args:
            endpoint: The endpoint path.
            json_body: The JSON request body.

        Returns:
            Parsed JSON response.
        """
        return self._request("POST", endpoint, json_body=json_body)

    def put(self, endpoint: str, json_body: dict[str, Any] | None = None) -> Any:
        """Make a raw PUT request to any V1 endpoint.

        Args:
            endpoint: The endpoint path.
            json_body: The JSON request body.

        Returns:
            Parsed JSON response.
        """
        return self._request("PUT", endpoint, json_body=json_body)

    def delete(self, endpoint: str) -> None:
        """Make a raw DELETE request to any V1 endpoint.

        Args:
            endpoint: The endpoint path.
        """
        self._request("DELETE", endpoint)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> NationBuilderClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
