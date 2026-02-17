from __future__ import annotations

from pathlib import Path

import pytest

from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import InlineCodeProvider
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return project


@pytest.fixture
def cairn_home(tmp_path: Path) -> Path:
    home = tmp_path / "cairn-home"
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture
async def orchestrator(project_root: Path, cairn_home: Path) -> CairnOrchestrator:
    orch = CairnOrchestrator(
        project_root=project_root,
        cairn_home=cairn_home,
        config=OrchestratorSettings(max_concurrent_agents=1),
        executor_settings=ExecutorSettings(),
        code_provider=InlineCodeProvider(),
    )
    await orch.initialize()
    try:
        yield orch
    finally:
        await orch.shutdown()
