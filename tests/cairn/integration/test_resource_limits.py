from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path

import pytest

from cairn.core.exceptions import ResourceLimitError
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import InlineCodeProvider
from cairn.runtime.agent import AgentState
from cairn.runtime.sandbox import BwrapExecutor, SandboxExecutionError
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings
from tests.cairn.sandbox_env import BWRAP, SANDBOX_PYTHON, requires_sandbox


class TimedOutExecutor:
    def __init__(self, **kwargs: object) -> None:
        _ = kwargs

    async def run(self, *, code: str, task: str) -> object:
        from cairn.core.exceptions import TimeoutError as CairnTimeoutError

        _ = code
        _ = task
        raise CairnTimeoutError("Operation exceeded timeout of 0.01s", error_code="EXECUTION_TIMEOUT")


async def _wait_for_state(
    orch: CairnOrchestrator,
    agent_id: str,
    state: AgentState,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ctx = orch.active_agents.get(agent_id)
        if ctx and ctx.state is state:
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"Agent {agent_id} did not reach state {state}")


async def _cancel_worker(orch: CairnOrchestrator) -> None:
    if orch._worker_task and not orch._worker_task.done():
        orch._worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await orch._worker_task


@pytest.mark.asyncio
@pytest.mark.integration
async def test_queue_size_limit_rejects_overflow(tmp_path: Path) -> None:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "home",
        config=OrchestratorSettings(max_queue_size=1),
        code_provider=InlineCodeProvider(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    await orch.initialize()
    await _cancel_worker(orch)

    try:
        await orch.spawn_agent("first")
        with pytest.raises(ResourceLimitError):
            await orch.spawn_agent("second")
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_execution_timeout_marks_agent_errored(tmp_path: Path) -> None:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "home",
        config=OrchestratorSettings(max_concurrent_agents=1),
        executor_settings=ExecutorSettings(max_execution_time=0.01),
        code_provider=InlineCodeProvider(),
        executor_factory=lambda **kwargs: TimedOutExecutor(**kwargs),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    await orch.initialize()

    try:
        agent_id = await orch.spawn_agent("sleep")
        await _wait_for_state(orch, agent_id, AgentState.ERRORED)
        ctx = orch.active_agents.get(agent_id)
        assert ctx is not None
        assert "timeout" in (ctx.error or "").lower()
    finally:
        await orch.shutdown()


@requires_sandbox
@pytest.mark.asyncio
@pytest.mark.integration
async def test_memory_limit_kills_oversized_task(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)

    settings = ExecutorSettings(
        bwrap_path=BWRAP,
        python_path=SANDBOX_PYTHON,
        max_execution_time=30.0,
        max_memory_bytes=64 * 1024 * 1024,
    )
    executor = BwrapExecutor(
        agent_id="agent-memory",
        workdir=tmp_path / "work",
        project_root=project,
        settings=settings,
    )

    # A 300 MB allocation far exceeds the 64 MB RLIMIT_DATA budget.
    code = "data = [0] * (300 * 1024 * 1024 // 8)\nsubmit_result(summary='done', changed_files=[])\n"
    with pytest.raises(SandboxExecutionError):
        await executor.run(code=code, task="memory")
