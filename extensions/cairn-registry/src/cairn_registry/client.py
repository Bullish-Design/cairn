"""HTTP client helpers for registry providers."""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from cairn.core.constants import (
    REGISTRY_ALLOWED_SCHEMES,
    REGISTRY_FETCH_TIMEOUT_SECONDS,
    REGISTRY_LOCALHOST_SCHEMES,
    REGISTRY_MAX_RESPONSE_BYTES,
)
from cairn.core.exceptions import CodeProviderError


@dataclass(frozen=True)
class RegistryClient:
    base_url: str

    def fetch_code(self, path: str) -> str:
        url = urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))
        _validate_url_scheme(url)

        with urllib.request.urlopen(url, timeout=REGISTRY_FETCH_TIMEOUT_SECONDS) as response:
            payload = response.read(REGISTRY_MAX_RESPONSE_BYTES + 1)
            if len(payload) > REGISTRY_MAX_RESPONSE_BYTES:
                raise CodeProviderError(f"registry response exceeds the {REGISTRY_MAX_RESPONSE_BYTES} byte cap")
            return payload.decode("utf-8")


def _validate_url_scheme(url: str) -> None:
    """Scheme/host policy (review §3.5): https only; http only for localhost."""
    parsed = urlparse(url)
    if parsed.scheme in REGISTRY_ALLOWED_SCHEMES:
        return
    if parsed.scheme in REGISTRY_LOCALHOST_SCHEMES and parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return
    raise CodeProviderError(f"registry URL scheme not allowed: {parsed.scheme!r} ({url})")
