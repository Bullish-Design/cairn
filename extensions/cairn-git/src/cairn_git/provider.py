"""Git-backed code provider for Cairn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cairn.core.exceptions import CodeProviderError
from cairn.providers.providers import CodeProvider
from cairn_git.cache import ensure_repo_cache, parse_git_reference


class GitCodeProvider(CodeProvider):
    """Load `.pym` scripts from git references."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path.home() / ".cache" / "cairn" / "git"

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        _ = context
        try:
            git_ref = parse_git_reference(reference)
        except ValueError as exc:
            raise CodeProviderError(str(exc)) from exc

        repo_path = ensure_repo_cache(git_ref, self.cache_dir)
        file_path = repo_path / git_ref.file_path

        if not file_path.exists():
            raise CodeProviderError(f"Git reference not found: {git_ref.file_path}")

        return file_path.read_text(encoding="utf-8")

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return True, None
