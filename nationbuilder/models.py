"""Data models for NationBuilder V1 API resources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Address:
    """Represents an address in NationBuilder."""

    address1: str | None = None
    address2: str | None = None
    address3: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country_code: str | None = None
    lat: float | None = None
    lng: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Address | None:
        """Create an Address from a V1 API dict, or None if data is empty."""
        if not data:
            return None
        return cls(
            address1=data.get("address1"),
            address2=data.get("address2"),
            address3=data.get("address3"),
            city=data.get("city"),
            state=data.get("state"),
            zip=data.get("zip"),
            country_code=data.get("country_code"),
            lat=data.get("lat"),
            lng=data.get("lng"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dict for API requests, omitting None values."""
        result: dict[str, Any] = {}
        if self.address1 is not None:
            result["address1"] = self.address1
        if self.address2 is not None:
            result["address2"] = self.address2
        if self.address3 is not None:
            result["address3"] = self.address3
        if self.city is not None:
            result["city"] = self.city
        if self.state is not None:
            result["state"] = self.state
        if self.zip is not None:
            result["zip"] = self.zip
        if self.country_code is not None:
            result["country_code"] = self.country_code
        if self.lat is not None:
            result["lat"] = self.lat
        if self.lng is not None:
            result["lng"] = self.lng
        return result


@dataclass
class Person:
    """Represents a person (signup) in NationBuilder V1 API."""

    id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: str | None = None
    email1: str | None = None
    email2: str | None = None
    email3: str | None = None
    email4: str | None = None
    phone: str | None = None
    mobile: str | None = None
    work_phone_number: str | None = None
    sex: str | None = None
    birthdate: str | None = None
    support_level: int | None = None
    note: str | None = None
    occupation: str | None = None
    employer: str | None = None
    party: str | None = None
    tags: list[str] = field(default_factory=list)
    primary_address: Address | None = None
    home_address: Address | None = None
    registered_address: Address | None = None
    mailing_address: Address | None = None
    work_address: Address | None = None
    billing_address: Address | None = None
    external_id: str | None = None
    language: str | None = None
    is_volunteer: bool | None = None
    is_donor: bool | None = None
    is_supporter: bool | None = None
    is_deceased: bool | None = None
    do_not_call: bool | None = None
    do_not_contact: bool | None = None
    email_opt_in: bool | None = None
    mobile_opt_in: bool | None = None
    signup_type: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    recruiter_id: int | None = None
    parent_id: int | None = None
    # Store the full raw response for access to any field
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Person:
        """Create a Person from a V1 API response dict.

        Args:
            data: A flat person dict from the V1 API.

        Returns:
            A populated Person instance.
        """
        return cls(
            id=data.get("id"),
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            full_name=data.get("full_name"),
            email=data.get("email"),
            email1=data.get("email1"),
            email2=data.get("email2"),
            email3=data.get("email3"),
            email4=data.get("email4"),
            phone=data.get("phone"),
            mobile=data.get("mobile"),
            work_phone_number=data.get("work_phone_number"),
            sex=data.get("sex"),
            birthdate=data.get("birthdate"),
            support_level=data.get("support_level"),
            note=data.get("note"),
            occupation=data.get("occupation"),
            employer=data.get("employer"),
            party=data.get("party"),
            tags=data.get("tags", []),
            primary_address=Address.from_dict(data.get("primary_address")),
            home_address=Address.from_dict(data.get("home_address")),
            registered_address=Address.from_dict(data.get("registered_address")),
            mailing_address=Address.from_dict(data.get("mailing_address")),
            work_address=Address.from_dict(data.get("work_address")),
            billing_address=Address.from_dict(data.get("billing_address")),
            external_id=data.get("external_id"),
            language=data.get("language"),
            is_volunteer=data.get("is_volunteer"),
            is_donor=data.get("is_donor"),
            is_supporter=data.get("is_supporter"),
            is_deceased=data.get("is_deceased"),
            do_not_call=data.get("do_not_call"),
            do_not_contact=data.get("do_not_contact"),
            email_opt_in=data.get("email_opt_in"),
            mobile_opt_in=data.get("mobile_opt_in"),
            signup_type=data.get("signup_type"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            recruiter_id=data.get("recruiter_id"),
            parent_id=data.get("parent_id"),
            _raw=data,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dict for create/update API requests.

        Only includes non-None fields to avoid overwriting existing data.

        Returns:
            A dict suitable for the V1 API request body.
        """
        result: dict[str, Any] = {}
        if self.first_name is not None:
            result["first_name"] = self.first_name
        if self.last_name is not None:
            result["last_name"] = self.last_name
        if self.email is not None:
            result["email"] = self.email
        if self.phone is not None:
            result["phone"] = self.phone
        if self.mobile is not None:
            result["mobile"] = self.mobile
        if self.work_phone_number is not None:
            result["work_phone_number"] = self.work_phone_number
        if self.sex is not None:
            result["sex"] = self.sex
        if self.birthdate is not None:
            result["birthdate"] = self.birthdate
        if self.support_level is not None:
            result["support_level"] = self.support_level
        if self.note is not None:
            result["note"] = self.note
        if self.occupation is not None:
            result["occupation"] = self.occupation
        if self.employer is not None:
            result["employer"] = self.employer
        if self.party is not None:
            result["party"] = self.party
        if self.tags:
            result["tags"] = self.tags
        if self.external_id is not None:
            result["external_id"] = self.external_id
        if self.language is not None:
            result["language"] = self.language
        if self.is_volunteer is not None:
            result["is_volunteer"] = self.is_volunteer
        if self.do_not_call is not None:
            result["do_not_call"] = self.do_not_call
        if self.do_not_contact is not None:
            result["do_not_contact"] = self.do_not_contact
        if self.email_opt_in is not None:
            result["email_opt_in"] = self.email_opt_in
        if self.mobile_opt_in is not None:
            result["mobile_opt_in"] = self.mobile_opt_in
        if self.signup_type is not None:
            result["signup_type"] = self.signup_type
        if self.recruiter_id is not None:
            result["recruiter_id"] = self.recruiter_id
        if self.parent_id is not None:
            result["parent_id"] = self.parent_id
        # Address fields
        if self.home_address:
            result["home_address"] = self.home_address.to_dict()
        if self.registered_address:
            result["registered_address"] = self.registered_address.to_dict()
        if self.mailing_address:
            result["mailing_address"] = self.mailing_address.to_dict()
        if self.work_address:
            result["work_address"] = self.work_address.to_dict()
        if self.billing_address:
            result["billing_address"] = self.billing_address.to_dict()
        return result

    def get_raw(self, key: str, default: Any = None) -> Any:
        """Access any field from the raw API response.

        Args:
            key: The field name from the V1 API response.
            default: Default value if key not present.

        Returns:
            The raw value from the API.
        """
        return self._raw.get(key, default)


@dataclass
class PaginatedResponse:
    """Represents a paginated V1 API response.

    V1 pagination uses __nonce and __token parameters with next/prev URLs.
    """

    results: list[dict[str, Any]]
    next_url: str | None = None
    prev_url: str | None = None

    @classmethod
    def from_v1_response(cls, data: dict[str, Any]) -> PaginatedResponse:
        """Create from a V1 API paginated response.

        Args:
            data: The full JSON response with 'results', 'next', 'prev' keys.

        Returns:
            A PaginatedResponse instance.
        """
        return cls(
            results=data.get("results", []),
            next_url=data.get("next"),
            prev_url=data.get("prev"),
        )

    @property
    def has_more(self) -> bool:
        """Whether there are more pages available."""
        return self.next_url is not None

    @property
    def count(self) -> int:
        """Number of results on the current page."""
        return len(self.results)

    def as_people(self) -> list[Person]:
        """Convert all results to Person instances.

        Returns:
            A list of Person instances.
        """
        return [Person.from_dict(r) for r in self.results]
