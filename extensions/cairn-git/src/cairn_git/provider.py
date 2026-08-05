"""Git-backed code provider for Cairn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cairn.core.exceptions import CodeProviderError
from cairn.providers.providers import CodeProvider
from cairn_git.cache import ensure_repo_cache, parse_git_reference


class GitCodeProvider(CodeProvider):
    """Load Python scripts from git references."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path.home() / ".cache" / "cairn" / "git"

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        _ = context
        try:
            git_ref = parse_git_reference(reference)
        except ValueError as exc:
            raise CodeProviderError(str(exc)) from exc

        repo_path = ensure_repo_cache(git_ref, self.cache_dir)
        file_path = _resolve_confined(repo_path, git_ref.file_path)

        if not file_path.exists():
            raise CodeProviderError(f"Git reference not found: {git_ref.file_path}")

        return file_path.read_text(encoding="utf-8")


def _resolve_confined(repo_path: Path, fragment: str) -> Path:
    """Resolve a file fragment confined beneath the cloned repository.

    ``../`` fragments, absolute paths, and symlink escapes must never read
    host files outside the cache (review §3.5).
    """
    if not fragment or fragment.startswith("/") or ".." in Path(fragment).parts:
        raise CodeProviderError(f"Git file fragment escapes the repository: {fragment!r}")
    resolved_repo = repo_path.resolve()
    resolved_file = (repo_path / fragment).resolve()
    if not resolved_file.is_relative_to(resolved_repo):
        raise CodeProviderError(f"Git file fragment escapes the repository: {fragment!r}")
    return resolved_file

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return True, None
