"""Project filesystem watcher that syncs changes into stable workspace."""

from __future__ import annotations

from pathlib import Path
import logging

from fsdantic import Workspace
from watchfiles import Change, awatch

logger = logging.getLogger(__name__)


class FileWatcher:
    """Watch filesystem changes and mirror them into stable workspace."""

    def __init__(self, project_root: Path, workspace: Workspace):
        self.project_root = Path(project_root)
        self.workspace = workspace
        self.ignore_patterns = [".agentfs", ".git", ".jj", "__pycache__", "node_modules"]

    async def watch(self) -> None:
        async for changes in awatch(self.project_root):
            for change_type, path_str in changes:
                await self.handle_change(change_type, Path(path_str))

    async def handle_change(self, change_type: Change, path: Path) -> None:
        if self.should_ignore(path) or path.is_dir():
            return

        rel_path = path.relative_to(self.project_root).as_posix()

        if change_type == Change.deleted:
            if await self.workspace.files.exists(rel_path):
                await self.workspace.files.remove(rel_path)
            return

        if not path.exists():
            return

        await self.workspace.files.write(rel_path, path.read_bytes(), mode="binary")

    def should_ignore(self, path: Path) -> bool:
        try:
            rel_parts = path.relative_to(self.project_root).parts
        except ValueError:
            logger.warning(
                "Path outside project root, ignoring",
                extra={"path": str(path), "project_root": str(self.project_root)},
            )
            return True

        return any(part in self.ignore_patterns for part in rel_parts)
