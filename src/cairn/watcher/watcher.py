"""Project filesystem watcher that syncs changes into stable workspace."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fsdantic import Workspace
from watchfiles import Change, DefaultFilter, awatch

from cairn.core.constants import DEFAULT_MAX_SYNC_FILE_BYTES

logger = logging.getLogger(__name__)

_SEED_BATCH = 128


@dataclass(frozen=True)
class SyncStats:
    written: int
    skipped_large: int
    failed: int


# Cairn-specific directory exclusions on top of DefaultFilter's own set
# (__pycache__, .git, .hg, .svn, .tox, .venv, .idea, node_modules,
# .mypy_cache, .pytest_cache, .hypothesis).
EXTRA_IGNORE_DIRS: tuple[str, ...] = (
    ".agentfs", ".jj", ".devenv", ".direnv", "venv",
    ".ruff_cache", ".coverage", "htmlcov",
    "dist", "build", "target", ".eggs",
)

DEFAULT_IGNORE_SUFFIXES: tuple[str, ...] = (
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
    ".so", ".dylib", ".dll", ".o", ".a", ".pyc", ".pyo",
)


class ProjectFilter(DefaultFilter):
    """DefaultFilter plus Cairn's own exclusions."""

    def __init__(self, project_root: Path, extra_ignore_dirs: list[str] | None = None) -> None:
        self.project_root = project_root
        extra = tuple(extra_ignore_dirs or ())
        super().__init__(ignore_dirs=(*DefaultFilter.ignore_dirs, *EXTRA_IGNORE_DIRS, *extra))

    def __call__(self, change: Change, path: str) -> bool:
        if not super().__call__(change, path):
            return False
        return self.allows(Path(path))

    def allows(self, path: Path) -> bool:
        """Predicate shared by the watcher and the initial sync.

        Note this is a *name*-based decision only - it must stay cheap enough
        to run on every filesystem event.  Size is checked separately, at the
        point of reading, because a path's size changes over time.
        """
        if path.suffix in DEFAULT_IGNORE_SUFFIXES:
            return False
        try:
            rel = path.relative_to(self.project_root)
        except ValueError:
            return False
        # DefaultFilter matches ancestor components of the absolute path too;
        # re-check against the project-relative parts so a project living under
        # a directory named e.g. "build" is not excluded wholesale.
        return not any(part in self._ignore_dirs for part in rel.parts)


class FileWatcher:
    """Watch filesystem changes and mirror them into stable workspace."""

    def __init__(
        self,
        project_root: Path,
        workspace: Workspace,
        *,
        max_file_bytes: int = DEFAULT_MAX_SYNC_FILE_BYTES,
        extra_ignore_dirs: list[str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = workspace
        self.max_file_bytes = max_file_bytes
        self.filter = ProjectFilter(self.project_root, extra_ignore_dirs)

    async def watch(self) -> None:
        async for changes in awatch(self.project_root, watch_filter=self.filter):
            for change_type, path_str in changes:
                await self.handle_change(change_type, Path(path_str))

    def _collect(self) -> tuple[list[Path], int]:
        """Walk the project tree (blocking — call via to_thread)."""
        paths: list[Path] = []
        skipped = 0
        for path in sorted(self.project_root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if not self.filter.allows(path):
                continue
            try:
                if path.stat().st_size > self.max_file_bytes:
                    skipped += 1
                    continue
            except OSError:
                continue
            paths.append(path)
        return paths, skipped

    @staticmethod
    def _read_batch(root: Path, chunk: list[Path]) -> list[tuple[str, str | bytes | dict[str, Any] | list[Any]]]:
        items: list[tuple[str, str | bytes | dict[str, Any] | list[Any]]] = []
        for path in chunk:
            try:
                items.append((path.relative_to(root).as_posix(), path.read_bytes()))
            except OSError:
                continue
        return items

    async def initial_sync(self) -> SyncStats:
        """Mirror the current project tree into the stable workspace.

        Runs once at orchestrator startup so agents materialize a workspace
        that reflects the project, not an empty tree.
        """
        paths, skipped = await asyncio.to_thread(self._collect)
        written = failed = 0
        for start in range(0, len(paths), _SEED_BATCH):
            chunk = paths[start : start + _SEED_BATCH]
            items: list[tuple[str, str | bytes | dict[str, Any] | list[Any]]] = await asyncio.to_thread(
                self._read_batch, self.project_root, chunk
            )
            result = await self.workspace.files.write_many(items, mode="binary", concurrency_limit=1)
            for item in result.items:
                if item.ok:
                    written += 1
                else:
                    failed += 1
                    logger.warning(
                        "Initial sync failed for path",
                        extra={"path": item.key_or_path, "error": item.error},
                    )
        stats = SyncStats(written=written, skipped_large=skipped, failed=failed)
        logger.info("Initial project sync complete", extra=stats.__dict__)
        return stats

    async def handle_change(self, change_type: Change, path: Path) -> None:
        if not self.filter.allows(path) or path.is_dir():
            return

        rel_path = path.relative_to(self.project_root).as_posix()

        if change_type == Change.deleted:
            if await self.workspace.files.exists(rel_path):
                await self.workspace.files.remove(rel_path)
            return

        try:
            if path.stat().st_size > self.max_file_bytes:
                logger.debug("Skipping oversized file", extra={"path": rel_path})
                return
        except OSError:
            return                       # vanished between event and stat

        content = await asyncio.to_thread(path.read_bytes)
        await self.workspace.files.write(rel_path, content, mode="binary")
