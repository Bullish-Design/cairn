"""HTTP client helpers for registry providers."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin
import urllib.request


@dataclass(frozen=True)
class RegistryClient:
    base_url: str

    def fetch_code(self, path: str) -> str:
        url = urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))
        with urllib.request.urlopen(url) as response:
            return response.read().decode("utf-8")
