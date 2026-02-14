"""Project filesystem watcher that syncs changes into stable AgentFS."""

from __future__ import annotations

from pathlib import Path

from agentfs_sdk import AgentFS
from watchfiles import Change, awatch


class FileWatcher:
    """Watch filesystem changes and mirror them into stable AgentFS."""

    def __init__(self, project_root: Path, stable: AgentFS):
        self.project_root = Path(project_root)
        self.stable = stable
        self.ignore_patterns = [".agentfs", ".git", ".jj", "__pycache__", "node_modules"]

    async def watch(self) -> None:
        """Watch project root and apply updates to stable layer."""
        async for changes in awatch(self.project_root):
            for change_type, path_str in changes:
                await self.handle_change(change_type, Path(path_str))

    async def handle_change(self, change_type: Change, path: Path) -> None:
        """Handle a single file change event."""
        if self.should_ignore(path) or path.is_dir():
            return

        rel_path = path.relative_to(self.project_root).as_posix()

        if change_type == Change.deleted:
            await self._delete_from_stable(rel_path)
            return

        if not path.exists():
            return

        await self.stable.fs.write_file(rel_path, path.read_bytes())

    async def _delete_from_stable(self, rel_path: str) -> None:
        """Delete file from stable AgentFS using whichever sdk method exists."""
        for method_name in ("delete_file", "unlink", "rm", "remove"):
            method = getattr(self.stable.fs, method_name, None)
            if method is None:
                continue

            await method(rel_path)
            return

    def should_ignore(self, path: Path) -> bool:
        """Check whether a path is ignored by watcher sync."""
        try:
            rel_parts = path.relative_to(self.project_root).parts
        except ValueError:
            return True

        return any(part in self.ignore_patterns for part in rel_parts)
