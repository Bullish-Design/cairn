"""Materialize workspace state to local preview workspaces."""

from __future__ import annotations

import shutil
from pathlib import Path

from fsdantic import Workspace


class WorkspaceMaterializer:
    """Materialize stable + overlay workspace contents to disk for previews."""

    def __init__(self, cairn_home: Path, stable_workspace: Workspace | None = None):
        self.workspace_dir = Path(cairn_home) / "workspaces"
        self.stable_workspace = stable_workspace

    async def materialize(self, agent_id: str, overlay_workspace: Workspace) -> Path:
        workspace = self.workspace_dir / agent_id
        workspace.mkdir(parents=True, exist_ok=True)

        await overlay_workspace.materialize.to_disk(
            target_path=workspace,
            base=self.stable_workspace,
            clean=True,
            allow_root=self.workspace_dir,
        )

        return workspace

    async def diff(self, overlay_workspace: Workspace) -> list[str]:
        if self.stable_workspace is None:
            return []

        changes = await overlay_workspace.materialize.diff(base=self.stable_workspace)
        return [change.path for change in changes]

    async def cleanup(self, agent_id: str) -> None:
        workspace = self.workspace_dir / agent_id
        if workspace.exists():
            shutil.rmtree(workspace)
