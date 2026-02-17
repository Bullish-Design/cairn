from __future__ import annotations

from pathlib import Path

import pytest

from cairn.orchestrator.orchestrator import CairnOrchestrator


@pytest.mark.asyncio
async def test_orchestrator_shutdown_closes_workspaces(tmp_path: Path) -> None:
    orch = CairnOrchestrator(project_root=tmp_path / "project", cairn_home=tmp_path / "home")

    await orch.initialize()

    assert orch.workspace_manager._active_workspaces

    await orch.shutdown()

    assert len(orch.workspace_manager._active_workspaces) == 0
