from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.core.exceptions import ProviderError
from cairn.orchestrator.lifecycle import SUBMISSION_KEY, LifecycleStore, SubmissionRecord
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.sandbox import SandboxResult


async def _safe_close(workspace: object) -> None:
    close_method = getattr(workspace, "close", None)
    if close_method is None:
        return
    try:
        await close_method()
    except Exception:  # noqa: BLE001
        return


class StubCodeProvider:
    def __init__(
        self,
        code: str = "x = 1",
        is_valid: bool = True,
        error: str | None = None,
        raise_on_get: bool = False,
    ) -> None:
        self.code = code
        self.is_valid = is_valid
        self.error = error
        self.raise_on_get = raise_on_get

    async def get_code(self, reference: str, context: dict) -> str:
        _ = reference
        _ = context
        if self.raise_on_get:
            raise ProviderError("provider failed")
        return self.code

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return self.is_valid, self.error


class PhaseExecutor:
    """Fake sandbox executor that records execution and returns a submission."""

    def __init__(self, **kwargs: object) -> None:
        _ = kwargs
        self.ran = False
        self.code: str | None = None
        self.task: str | None = None

    async def run(self, *, code: str, task: str) -> SandboxResult:
        self.ran = True
        self.code = code
        self.task = task
        return SandboxResult(
            submission={"summary": "done", "changed_files": ["notes.txt"], "submitted_at": 1.0},
            changes={"written": [], "deleted": []},
            log="",
        )


async def _setup_orchestrator(
    tmp_path: Path, code_provider: StubCodeProvider | None = None
) -> tuple[CairnOrchestrator, AgentContext, object, object, object]:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        code_provider=code_provider or StubCodeProvider(),
        executor_factory=lambda **kwargs: PhaseExecutor(**kwargs),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    orch.cairn_home.mkdir(parents=True, exist_ok=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True, exist_ok=True)
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    agent_db_path = orch.agentfs_dir / "agent-phase.db"
    agent_ws = await Fsdantic.open(path=str(agent_db_path))

    orch.stable = stable
    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)
    await orch.workspace_cache.put(str(agent_db_path), agent_ws)

    ctx = AgentContext(
        agent_id="agent-phase",
        task="phase test",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    return orch, ctx, stable, bin_ws, agent_ws


@pytest.mark.asyncio
async def test_generate_code_phase(tmp_path: Path) -> None:
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    try:
        await orch._transition_agent_state(ctx, AgentState.GENERATING)
        generated = await orch._generate_code(ctx)

        assert generated == "x = 1"
        assert ctx.generated_code == "x = 1"
        assert ctx.state is AgentState.GENERATING
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_generate_code_handles_provider_error(tmp_path: Path) -> None:
    provider = StubCodeProvider(raise_on_get=True)
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path, provider)

    try:
        await orch._transition_agent_state(ctx, AgentState.GENERATING)
        generated = await orch._generate_code(ctx)

        assert generated is None
        assert ctx.state is AgentState.ERRORED
        assert "provider failed" in (ctx.error or "")
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_execute_code_phase(tmp_path: Path) -> None:
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    try:
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
        result = await orch._execute_code(ctx, "x = 1")

        assert result.submission is not None
        assert result.submission["summary"] == "done"

        executor = orch.executor_factory()
        assert executor.ran is False  # each call gets a fresh executor instance
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_execute_code_passes_code_and_task_to_executor(tmp_path: Path) -> None:
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)
    executor = PhaseExecutor()
    orch.executor_factory = lambda **kwargs: executor

    try:
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
        await orch._execute_code(ctx, "x = 2")

        assert executor.ran is True
        assert executor.code == "x = 2"
        assert executor.task == "phase test"
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_submit_results_phase_persists_submission(tmp_path: Path) -> None:
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    try:
        ctx.submission = {"summary": "done", "changed_files": ["notes.txt"], "submitted_at": 1.0}

        await orch._submit_results(ctx)

        submission_repo = agent_ws.kv.repository(prefix="", model_type=SubmissionRecord)
        saved = await submission_repo.load(SUBMISSION_KEY)
        assert saved is not None
        assert saved.agent_id == ctx.agent_id
        assert saved.submission["summary"] == "done"
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)
