"""API-key authentication.

Keys are configured as ``principal:key`` pairs. Only SHA-256 digests are kept in memory and
every comparison is constant-time. The principal name becomes the owner of the jobs created with
that key, and every job query is scoped to it.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from autoswe.config import Settings

ANONYMOUS = "anonymous"


@dataclass(frozen=True, slots=True)
class Principal:
    name: str


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class ApiKeyAuthenticator:
    def __init__(self, keys: dict[str, str], *, disabled: bool = False) -> None:
        self.disabled = disabled
        self._entries = [(_digest(key), principal) for key, principal in keys.items()]

    @classmethod
    def from_settings(cls, settings: Settings) -> ApiKeyAuthenticator:
        return cls(settings.parsed_api_keys(), disabled=settings.auth_disabled)

    def authenticate(self, presented: str | None) -> Principal | None:
        if self.disabled:
            return Principal(ANONYMOUS)
        if not presented:
            return None
        candidate = _digest(presented)
        match: str | None = None
        for digest, principal in self._entries:  # no early exit: timing is key-independent
            if hmac.compare_digest(digest, candidate):
                match = principal
        return Principal(match) if match else None


def extract_api_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer" and credentials.strip():
            return credentials.strip()
    return None
