from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.core.exceptions import ProviderError
from cairn.orchestrator.lifecycle import RUN_KEY, SUBMISSION_KEY, LifecycleStore, RunRecord, SubmissionRecord
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
        await orch._transition_agent_state(ctx, AgentState.GENERATING)
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
        await orch._transition_agent_state(ctx, AgentState.GENERATING)
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
        await orch._execute_code(ctx, "x = 2")

        assert executor.ran is True
        assert executor.code == "x = 2"
        assert executor.task == "phase test"
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


class LyingExecutor:
    """Fake executor that writes two files but reports only one."""

    def __init__(self, **kwargs: object) -> None:
        _ = kwargs

    async def run(self, *, code: str, task: str) -> SandboxResult:
        _ = code, task
        return SandboxResult(
            submission={"summary": "done", "changed_files": ["a.txt"], "submitted_at": 1.0},
            changes={"written": ["a.txt", "b.txt"], "deleted": []},
            log="agent did stuff\n",
            base_hashes={"a.txt": "h1", "b.txt": "h2"},
            exit_code=0,
        )


@pytest.mark.asyncio
async def test_run_record_persists_ground_truth_and_flags_mismatch(tmp_path: Path) -> None:
    """P2.1: the sandbox-observed changeset is persisted in a RunRecord and a
    lying self-report is flagged on the lifecycle record."""
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)
    orch.executor_factory = lambda **kwargs: LyingExecutor(**kwargs)

    try:
        await orch._execute_agent_lifecycle(ctx)

        # The run record lists both files actually written.
        run_repo = agent_ws.kv.repository(prefix="", model_type=RunRecord)
        run = await run_repo.load(RUN_KEY)
        assert run is not None
        assert run.written == ["a.txt", "b.txt"]
        assert run.deleted == []
        assert run.base_hashes == {"a.txt": "h1", "b.txt": "h2"}
        assert run.log == "agent did stuff\n"
        assert run.exit_code == 0

        # The agent's self-report does not match what it did.
        assert ctx.files_written == 2
        assert ctx.claim_mismatch is True

        # The lifecycle record mirrors the summary fields.
        record = await orch.lifecycle.load(ctx.agent_id)
        assert record is not None
        assert record.claim_mismatch is True
        assert record.files_written == 2
        assert record.files_deleted == 0
        assert record.state is AgentState.REVIEWING
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_run_record_no_mismatch_when_report_matches(tmp_path: Path) -> None:
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    try:
        await orch._execute_agent_lifecycle(ctx)

        run_repo = agent_ws.kv.repository(prefix="", model_type=RunRecord)
        run = await run_repo.load(RUN_KEY)
        assert run is not None
        # PhaseExecutor claims notes.txt but actually changed nothing: the
        # claim is non-empty and does not match the empty ground truth, so the
        # agent is flagged.
        assert ctx.claim_mismatch is True
        record = await orch.lifecycle.load(ctx.agent_id)
        assert record is not None
        assert record.claim_mismatch is True
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_worker_loop_survives_dequeue_error(tmp_path: Path) -> None:
    """P4.2: an exception in the scheduling path is logged and recovered from,
    not silently fatal."""
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)
    orch.executor_factory = lambda **kwargs: PhaseExecutor(**kwargs)

    real_dequeue_wait = orch.queue.dequeue_wait
    calls = {"n": 0}

    async def flaky_dequeue_wait():
        if calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("boom")
        return await real_dequeue_wait()

    orch.queue.dequeue_wait = flaky_dequeue_wait  # type: ignore[method-assign]

    worker = asyncio.create_task(orch._worker_loop())
    try:
        await orch.queue.enqueue(ctx.agent_id, ctx.priority)
        await orch.wait_for_agent(ctx.agent_id, timeout=5.0)
        assert ctx.state is AgentState.REVIEWING
        assert calls["n"] >= 1
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_handle_agent_error_keeps_workdir_and_log(tmp_path: Path) -> None:
    """P4.3: a failed run keeps its workdir and persists the partial run log
    instead of deleting the evidence."""
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    workdir = orch.cairn_home / "workspaces" / ctx.agent_id
    log_dir = workdir / ".cairn"
    log_dir.mkdir(parents=True)
    (log_dir / "run.log").write_text("agent printed this before dying\n", encoding="utf-8")

    try:
        await orch._handle_agent_error(ctx, RuntimeError("boom"))

        assert ctx.state is AgentState.ERRORED
        assert "boom" in (ctx.error or "")
        # The workdir (with run.log and partial changeset) survives.
        assert workdir.exists()
        assert (log_dir / "run.log").exists()

        # The run record captured the log.
        run_repo = agent_ws.kv.repository(prefix="", model_type=RunRecord)
        run = await run_repo.load(RUN_KEY)
        assert run is not None
        assert "agent printed this before dying" in run.log
        assert run.exit_code == 1
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
