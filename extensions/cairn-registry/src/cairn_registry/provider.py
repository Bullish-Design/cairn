"""Registry-backed code provider for Cairn."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cairn.core.exceptions import CodeProviderError
from cairn.providers.providers import CodeProvider
from cairn_registry.client import RegistryClient


class RegistryCodeProvider(CodeProvider):
    """Load `.pym` scripts from a remote registry."""

    def __init__(self, base_url: str | None = None, cache_dir: Path | None = None) -> None:
        self.base_url = base_url
        self.cache_dir = cache_dir

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        _ = context
        base_url, path = self._resolve_reference(reference)
        client = RegistryClient(base_url=base_url)
        return client.fetch_code(path)

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        if not code.strip():
            return False, "Registry returned empty code"
        return True, None

    def _resolve_reference(self, reference: str) -> tuple[str, str]:
        if reference.startswith("registry://"):
            parsed = urlparse(reference)
            if not parsed.netloc:
                raise CodeProviderError("Registry reference must include a host")
            path = parsed.path.lstrip("/")
            if not path:
                raise CodeProviderError("Registry reference must include a path")
            return f"https://{parsed.netloc}", path

        if not self.base_url:
            raise CodeProviderError("Registry provider requires a base URL or registry:// reference")

        return self.base_url, reference.lstrip("/")
