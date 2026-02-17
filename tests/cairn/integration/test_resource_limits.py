from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path

import pytest

from cairn.runtime.agent import AgentState
from cairn.core.exceptions import ResourceLimitError
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import InlineCodeProvider
from cairn.runtime.resource_limits import ResourceLimiter
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings


class CheckResult:
    def __init__(self, valid: bool) -> None:
        self.valid = valid
        self.errors: list[str] = []


class SlowScript:
    def check(self) -> CheckResult:
        return CheckResult(True)

    async def run(self, *, inputs: dict, externals: dict[str, object]) -> None:
        _ = inputs
        _ = externals
        await asyncio.sleep(0.05)


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
async def test_execution_timeout_marks_agent_errored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "home",
        config=OrchestratorSettings(max_concurrent_agents=1),
        executor_settings=ExecutorSettings(max_execution_time=0.01),
        code_provider=InlineCodeProvider(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    await orch.initialize()

    monkeypatch.setattr(
        "cairn.orchestrator.orchestrator._load_grail_script",
        lambda _: SlowScript(),
    )

    try:
        agent_id = await orch.spawn_agent("sleep")
        await _wait_for_state(orch, agent_id, AgentState.ERRORED)
        ctx = orch.active_agents.get(agent_id)
        assert ctx is not None
        assert "timeout" in (ctx.error or "").lower()
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_memory_limit_marks_agent_errored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values = [10_000_000, 10_000_000, 10_000_000, 13_000_000]

    def fake_rss() -> int:
        return values.pop(0) if values else 5000

    from cairn.runtime import resource_limits

    class FastLimiter(ResourceLimiter):
        def __init__(self, *, timeout_seconds: float, max_memory_bytes: int) -> None:
            super().__init__(
                timeout_seconds=timeout_seconds,
                max_memory_bytes=max_memory_bytes,
                poll_interval_seconds=0.01,
            )

    monkeypatch.setattr(resource_limits, "_get_rss_bytes", fake_rss)
    monkeypatch.setattr("cairn.orchestrator.orchestrator.ResourceLimiter", FastLimiter)

    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "home",
        config=OrchestratorSettings(max_concurrent_agents=1),
        executor_settings=ExecutorSettings(max_execution_time=1.0, max_memory_bytes=1_048_576),
        code_provider=InlineCodeProvider(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    await orch.initialize()

    class NoOpScript:
        def check(self) -> CheckResult:
            return CheckResult(True)

        async def run(self, *, inputs: dict, externals: dict[str, object]) -> None:
            _ = inputs
            _ = externals
            await asyncio.sleep(0.05)

    monkeypatch.setattr(
        "cairn.orchestrator.orchestrator._load_grail_script",
        lambda _: NoOpScript(),
    )

    try:
        agent_id = await orch.spawn_agent("memory")
        await _wait_for_state(orch, agent_id, AgentState.ERRORED)
        ctx = orch.active_agents.get(agent_id)
        assert ctx is not None
        assert "memory" in (ctx.error or "").lower()
    finally:
        await orch.shutdown()
