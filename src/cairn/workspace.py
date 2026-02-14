"""Materialize AgentFS state to local preview workspaces."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from agentfs_sdk import AgentFS


class WorkspaceMaterializer:
    """Materialize stable+overlay AgentFS contents to disk."""

    def __init__(self, cairn_home: Path, stable_fs: AgentFS | None = None):
        self.workspace_dir = Path(cairn_home) / "workspaces"
        self.stable_fs = stable_fs

    async def materialize(self, agent_id: str, agent_fs: AgentFS) -> Path:
        """Copy stable and overlay state to a local workspace directory."""
        workspace = self.workspace_dir / agent_id

        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        if self.stable_fs is not None:
            await self._copy_recursive(self.stable_fs, "/", workspace)

        await self._copy_recursive(agent_fs, "/", workspace)
        return workspace

    async def _copy_recursive(self, source_fs: AgentFS, src_path: str, dest_path: Path) -> None:
        """Recursively copy files from AgentFS into local filesystem."""
        entries = await self._readdir(source_fs, src_path)
        for entry in entries:
            name = getattr(entry, "name", None)
            if not name:
                continue

            source_child = f"{src_path.rstrip('/')}/{name}" if src_path != "/" else f"/{name}"
            local_child = dest_path / name

            if self._entry_is_dir(entry):
                local_child.mkdir(parents=True, exist_ok=True)
                await self._copy_recursive(source_fs, source_child, local_child)
                continue

            local_child.parent.mkdir(parents=True, exist_ok=True)
            file_bytes = await source_fs.fs.read_file(source_child)
            local_child.write_bytes(file_bytes)

    async def _readdir(self, source_fs: AgentFS, src_path: str) -> list[Any]:
        """Read directory with compatibility for '/' and '.' roots."""
        for candidate in (src_path, src_path.lstrip("/") or "."):
            try:
                return await source_fs.fs.readdir(candidate)
            except FileNotFoundError:
                continue
        return []

    def _entry_is_dir(self, entry: Any) -> bool:
        """Best-effort directory check for AgentFS readdir entries."""
        entry_type = getattr(entry, "type", None)
        return bool(
            entry_type == "directory"
            or entry_type == "dir"
            or getattr(entry, "is_directory", False)
            or getattr(entry, "is_dir", False)
        )

    async def cleanup(self, agent_id: str) -> None:
        """Remove a materialized workspace directory."""
        workspace = self.workspace_dir / agent_id
        if workspace.exists():
            shutil.rmtree(workspace)
