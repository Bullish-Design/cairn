"""Code provider abstractions for Cairn orchestration."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Protocol, cast, runtime_checkable
import inspect

from cairn.core.exceptions import CodeProviderError, ProviderError, RecoverableError
from cairn.utils.retry_utils import with_retry

PROVIDER_RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    RecoverableError,
    TimeoutError,
    ConnectionError,
)


@runtime_checkable
class CodeProvider(Protocol):
    """Protocol for sources that provide agent code."""

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        """Return source code for the given reference."""
        raise NotImplementedError

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
            raise ProviderError(f"Code reference not found: {path}")

        try:
            return await self._read_code_with_retry(path)
        except PROVIDER_RETRY_EXCEPTIONS:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ProviderError(f"Failed to read code from {path}: {exc}") from exc

    @with_retry(
        max_attempts=3,
        initial_delay=0.0,
        max_delay=0.0,
        retry_exceptions=PROVIDER_RETRY_EXCEPTIONS,
    )
    async def _read_code_with_retry(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return True, None

    def _resolve_path(self, reference: str) -> Path:
        if not reference.strip():
            raise ProviderError("Code reference must be non-empty")

        path = Path(reference)
        if path.suffix == "":
            path = path.with_suffix(".pym")

        if path.suffix != ".pym":
            raise ProviderError("Code reference must point to a .pym file")

        if not path.is_absolute():
            base_path = self.base_path or Path.cwd()
            path = base_path / path

        return path


class InlineCodeProvider:
    """Treat references as inline code snippets."""

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        _ = context
        if not reference.strip():
            raise ProviderError("Inline code reference must be non-empty")
        return reference

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return True, None


def resolve_code_provider(
    provider: str,
    *,
    project_root: Path | None,
    base_path: Path | None,
) -> CodeProvider:
    """Resolve a code provider by name or entry point."""
    if provider == "inline":
        return InlineCodeProvider()

    if provider == "file":
        resolved_base_path = base_path or project_root or Path(".")
        return FileCodeProvider(base_path=resolved_base_path)

    return _load_provider_from_entrypoints(
        provider,
        project_root=project_root,
        base_path=base_path,
    )


def _load_provider_from_entrypoints(
    provider: str,
    *,
    project_root: Path | None,
    base_path: Path | None,
) -> CodeProvider:
    entry_points = metadata.entry_points(group="cairn.providers")
    matches = [entry for entry in entry_points if entry.name == provider]

    if not matches:
        raise ProviderError(f"Unknown provider '{provider}'. Install the plugin package to use it.")

    if len(matches) > 1:
        raise ProviderError(f"Multiple providers registered for '{provider}'. Ensure only one plugin is installed.")

    factory = matches[0].load()
    return _instantiate_provider(factory, project_root=project_root, base_path=base_path)


def _instantiate_provider(
    factory: object,
    *,
    project_root: Path | None,
    base_path: Path | None,
) -> CodeProvider:
    if isinstance(factory, type):
        return _call_with_supported_kwargs(
            cast(Callable[..., Any], factory),
            project_root=project_root,
            base_path=base_path,
        )

    if callable(factory):
        return _call_with_supported_kwargs(
            cast(Callable[..., Any], factory),
            project_root=project_root,
            base_path=base_path,
        )

    raise ProviderError("Provider entry point must resolve to a callable or class")


def _call_with_supported_kwargs(
    callable_target: Callable[..., Any],
    *,
    project_root: Path | None,
    base_path: Path | None,
) -> CodeProvider:
    try:
        signature = inspect.signature(callable_target)
    except (TypeError, ValueError):
        return cast(CodeProvider, callable_target())

    kwargs: dict[str, object] = {}
    for key, value in {"project_root": project_root, "base_path": base_path}.items():
        if key in signature.parameters:
            kwargs[key] = value

    return cast(CodeProvider, callable_target(**kwargs))
