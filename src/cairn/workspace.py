"""Materialize AgentFS state to local preview workspaces."""

from __future__ import annotations

import shutil
from pathlib import Path

from agentfs_sdk import AgentFS
from fsdantic import ConflictResolution, Materializer


class WorkspaceMaterializer:
    """Materialize stable+overlay AgentFS contents to disk using fsdantic Materializer."""

    def __init__(self, cairn_home: Path, stable_fs: AgentFS | None = None):
        self.workspace_dir = Path(cairn_home) / "workspaces"
        self.stable_fs = stable_fs
        self.materializer = Materializer(
            conflict_resolution=ConflictResolution.OVERWRITE,
            progress_callback=None,  # Can add logging callback if needed
        )

    async def materialize(self, agent_id: str, agent_fs: AgentFS) -> Path:
        """Copy stable and overlay state to a local workspace directory.

        Uses fsdantic's Materializer for robust file copying with automatic
        conflict resolution and error handling.
        """
        workspace = self.workspace_dir / agent_id
        workspace.mkdir(parents=True, exist_ok=True)

        # Use fsdantic's Materializer to handle all the copying
        result = await self.materializer.materialize(
            agent_fs=agent_fs,
            target_path=workspace,
            base_fs=self.stable_fs,
            clean=True,  # Remove existing files before materializing
        )

        # Could log result statistics if needed:
        # print(f"Materialized {result.files_written} files ({result.bytes_written} bytes)")

        return workspace

    async def cleanup(self, agent_id: str) -> None:
        """Remove a materialized workspace directory."""
        workspace = self.workspace_dir / agent_id
        if workspace.exists():
            shutil.rmtree(workspace)
