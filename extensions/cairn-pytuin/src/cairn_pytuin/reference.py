"""Parse a cairn task reference into an Atuin KV coordinate."""

from __future__ import annotations

from dataclasses import dataclass

from cairn.core.exceptions import CodeProviderError


@dataclass(frozen=True)
class KvReference:
    """One Atuin KV coordinate: ``namespace/key``."""

    namespace: str
    key: str


def parse_kv_reference(reference: str, *, default_namespace: str) -> KvReference:
    """Parse ``namespace/key`` (or bare ``key``) into a coordinate.

    Malformed references fail loudly: silently querying the wrong namespace
    would surface as a confusing "not found" much later.
    """
    raw = (reference or "").strip()
    if not raw:
        raise CodeProviderError("Empty pytuin reference; expected 'namespace/key' or 'key'")

    parts = raw.split("/")
    if len(parts) > 2:
        raise CodeProviderError(f"Invalid pytuin reference {reference!r}: expected 'namespace/key' or 'key'")
    if any(not part.strip() for part in parts):
        raise CodeProviderError(f"Invalid pytuin reference {reference!r}: empty namespace or key segment")
    # Segments are interpolated into `atuin kv get --namespace <ns> <key>` as
    # bare argv elements, so anything starting with '-' would be parsed as a
    # flag by the atuin CLI (e.g. '--help' returns atuin's help text as task
    # code).  Not shell injection — pytuin uses shell=False — but the blast
    # radius is defined by atuin's CLI surface, which cairn does not control.
    if any(part.startswith("-") for part in parts):
        raise CodeProviderError(
            f"Invalid pytuin reference {reference!r}: segments must not start with "
            "'-' (they would be parsed as flags by the atuin CLI)"
        )

    if len(parts) == 1:
        return KvReference(namespace=default_namespace, key=parts[0].strip())
    return KvReference(namespace=parts[0].strip(), key=parts[1].strip())
