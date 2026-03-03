"""Workspace inspection utilities for debugging and CLI tools.

This module provides read-only inspection of workspace contents without
requiring full workspace context management. Useful for CLI tools,
debugging, and diagnostic purposes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fsdantic import Workspace


@dataclass
class WorkspaceStats:
    """Summary statistics for a workspace."""

    file_count: int
    dir_count: int
    total_bytes: int


class WorkspaceInspector:
    """Read-only workspace inspection utilities.

    Provides convenient methods for inspecting workspace contents
    without modifying them. Useful for CLI tools and debugging.

    This class can be used in two ways:

    1. With an existing workspace:
        ```python
        inspector = WorkspaceInspector(workspace)
        tree = await inspector.tree("/")
        ```

    2. Opening a workspace from path (owns the workspace lifecycle):
        ```python
        async with await WorkspaceInspector.from_path("/path/to/ws.db") as inspector:
            tree = await inspector.tree("/")
            stats = await inspector.stats()
        ```
    """

    def __init__(self, workspace: "Workspace"):
        """Create inspector from an existing workspace.

        Args:
            workspace: An open Workspace instance. The caller retains
                ownership and is responsible for closing it.
        """
        self._workspace = workspace
        self._owned = False

    @classmethod
    async def from_path(cls, path: Path | str) -> "WorkspaceInspector":
        """Create inspector by opening workspace at path.

        The returned inspector owns the workspace and will close it
        when used as an async context manager.

        Args:
            path: Path to the workspace database file

        Returns:
            A WorkspaceInspector that owns the workspace
        """
        from cairn.runtime.workspace_manager import open_workspace

        workspace = await open_workspace(path, readonly=True)
        inspector = cls(workspace)
        inspector._owned = True
        return inspector

    async def __aenter__(self) -> "WorkspaceInspector":
        """Enter async context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit async context, closing workspace if owned."""
        if self._owned:
            await self._workspace.close()

    @property
    def workspace(self) -> "Workspace":
        """Access underlying workspace."""
        return self._workspace

    async def tree(
        self,
        path: str = "/",
        max_depth: int | None = None,
    ) -> dict[str, Any]:
        """Get directory tree structure.

        Returns a nested dict with the shape:
        ``{ "name": str, "path": str, "type": "file"|"directory", "children": list }``

        Args:
            path: Root path for the tree
            max_depth: Maximum depth to traverse (None for unlimited)

        Returns:
            Tree structure as nested dictionaries
        """
        return await self._workspace.files.tree(path, max_depth=max_depth)

    async def list_dir(
        self,
        path: str = "/",
        include_stats: bool = False,
    ) -> list[str] | list[dict[str, Any]]:
        """List directory contents.

        Args:
            path: Directory path to list
            include_stats: If True, return dicts with name, size, type

        Returns:
            List of names (if include_stats=False) or list of dicts
        """
        names = await self._workspace.files.list_dir(path, output="name")

        if not include_stats:
            return names

        entries: list[dict[str, Any]] = []
        for name in names:
            full_path = f"{path.rstrip('/')}/{name}"
            try:
                stat = await self._workspace.files.stat(full_path)
                entries.append(
                    {
                        "name": name,
                        "size": stat.size,
                        "type": "file" if stat.is_file else "directory",
                    }
                )
            except Exception:
                entries.append({"name": name, "size": 0, "type": "unknown"})
        return entries

    async def read(self, path: str) -> str:
        """Read file contents as text.

        Args:
            path: File path to read

        Returns:
            File contents as string
        """
        return await self._workspace.files.read(path, mode="text")

    async def read_bytes(self, path: str) -> bytes:
        """Read file contents as bytes.

        Args:
            path: File path to read

        Returns:
            File contents as bytes
        """
        return await self._workspace.files.read(path, mode="binary")

    async def exists(self, path: str) -> bool:
        """Check if path exists in workspace.

        Args:
            path: Path to check

        Returns:
            True if path exists
        """
        return await self._workspace.files.exists(path)

    async def stats(self) -> WorkspaceStats:
        """Get workspace statistics.

        Returns:
            WorkspaceStats with file count, directory count, and total bytes
        """
        file_count = 0
        dir_count = 0
        total_bytes = 0

        async for path, file_stats in self._workspace.files.traverse_files("/", recursive=True, include_stats=True):
            if file_stats is not None:
                file_count += 1
                total_bytes += file_stats.size if hasattr(file_stats, "size") else 0

        # Count directories by listing all paths
        # Note: traverse_files only yields files, so we estimate dirs from tree
        try:
            tree = await self._workspace.files.tree("/", max_depth=100)
            dir_count = self._count_dirs_in_tree(tree)
        except Exception:
            dir_count = 0

        return WorkspaceStats(
            file_count=file_count,
            dir_count=dir_count,
            total_bytes=total_bytes,
        )

    def _count_dirs_in_tree(self, node: dict[str, Any]) -> int:
        """Count directories in a tree structure."""
        count = 0
        if node.get("type") == "directory":
            count = 1
        for child in node.get("children", []):
            count += self._count_dirs_in_tree(child)
        return count
