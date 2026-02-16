"""Code provider abstractions for Cairn orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class CodeProviderError(RuntimeError):
    """Raised when a code provider cannot supply code."""


@runtime_checkable
class CodeProvider(Protocol):
    """Protocol for sources that provide agent code."""

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        """Return source code for the given reference."""

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        """Validate code before execution."""
        return True, None


class FileCodeProvider:
    """Load .pym files from disk."""

    def __init__(self, base_path: Path | str | None = None) -> None:
        self.base_path = Path(base_path).expanduser().resolve() if base_path else None

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        _ = context
        path = self._resolve_path(reference)
        if not path.exists():
            raise CodeProviderError(f"Code reference not found: {path}")
        try:
            return path.read_text(encoding="utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            raise CodeProviderError(f"Failed to read code from {path}: {exc}") from exc

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return True, None

    def _resolve_path(self, reference: str) -> Path:
        if not reference.strip():
            raise CodeProviderError("Code reference must be non-empty")

        path = Path(reference)
        if path.suffix == "":
            path = path.with_suffix(".pym")

        if path.suffix != ".pym":
            raise CodeProviderError("Code reference must point to a .pym file")

        if not path.is_absolute():
            base_path = self.base_path or Path.cwd()
            path = base_path / path

        return path


class InlineCodeProvider:
    """Treat references as inline code snippets."""

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        _ = context
        if not reference.strip():
            raise CodeProviderError("Inline code reference must be non-empty")
        return reference

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return True, None
