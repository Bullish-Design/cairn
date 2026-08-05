from __future__ import annotations

import json
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.core.exceptions import RecoverableError
from cairn.core.exceptions import TimeoutError as CairnTimeoutError
from cairn.orchestrator.lifecycle import LifecycleStore
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.sandbox import SandboxExecutionError, SandboxResult


async def _safe_close(workspace: object) -> None:
    close_method = getattr(workspace, "close", None)
    if close_method is None:
        return
    try:
        await close_method()
    except Exception:  # noqa: BLE001
        return


class StubCodeProvider:
    def __init__(self, code: str = "x = 1", is_valid: bool = True, error: str | None = None) -> None:
        self.code = code
        self.is_valid = is_valid
        self.error = error
        self.context: dict | None = None
        self.reference: str | None = None

    async def get_code(self, reference: str, context: dict) -> str:
        self.reference = reference
        self.context = context
        return self.code

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return self.is_valid, self.error


class FakeExecutor:
    """Simulates the sandbox: writes files into the disposable workdir and
    records a submission (the changeset is computed from the diff, so the
    stub returns the paths it wrote)."""

    def __init__(
        self,
        *,
        agent_id: str,
        workdir: Path | str,
        project_root: Path | str,
        settings: object,
        **kwargs: object,
    ) -> None:
        self.agent_id = agent_id
        self.workdir = Path(workdir)
        self.project_root = Path(project_root)
        self.settings = settings
        self.code: str | None = None
        self.task: str | None = None
        self.fail_message: str | None = None
        self.timeout: bool = False

    async def run(self, *, code: str, task: str) -> SandboxResult:
        self.code = code
        self.task = task
        if self.timeout:
            raise CairnTimeoutError("Operation exceeded timeout of 0.01s", error_code="EXECUTION_TIMEOUT")
        if self.fail_message is not None:
            raise SandboxExecutionError(self.fail_message, error_code="SANDBOX_EXECUTION_FAILED")

        self.workdir.mkdir(parents=True, exist_ok=True)
        cairn_dir = self.workdir / ".cairn"
        cairn_dir.mkdir(parents=True, exist_ok=True)
        (cairn_dir / "task.py").write_text(code, encoding="utf-8")
        (cairn_dir / "submission.json").write_text(
            json.dumps({"summary": "ok", "changed_files": ["generated.txt"], "submitted_at": 1.0}),
            encoding="utf-8",
        )
        (self.workdir / "generated.txt").write_text("from sandbox", encoding="utf-8")

        return SandboxResult(
            submission={"summary": "ok", "changed_files": ["generated.txt"], "submitted_at": 1.0},
            changes={"written": ["generated.txt"], "deleted": []},
            log="",
        )


def fake_executor_factory(**executor_kwargs: object):
    """Build an executor factory yielding FakeExecutor instances."""

    def factory(**kwargs: object) -> FakeExecutor:
        instance = FakeExecutor(**kwargs)
        for key, value in executor_kwargs.items():
            setattr(instance, key, value)
        return instance

    return factory


async def _setup_orchestrator(
    tmp_path: Path,
    code_provider: StubCodeProvider | None = None,
    executor_factory=None,
) -> tuple[CairnOrchestrator, object, object, Path]:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        code_provider=code_provider or StubCodeProvider(),
        executor_factory=executor_factory or fake_executor_factory(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    orch.cairn_home.mkdir(parents=True, exist_ok=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True, exist_ok=True)
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    agent_db_path = tmp_path / "agent.db"
    agent_ws = await Fsdantic.open(path=str(agent_db_path))

    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)
    await orch.workspace_cache.put(str(agent_db_path), agent_ws)

    return orch, bin_ws, agent_ws, agent_db_path


async def _setup_orchestrator_with_agent_db(
    tmp_path: Path,
    agent_id: str,
    code_provider: StubCodeProvider | None = None,
    executor_factory=None,
) -> tuple[CairnOrchestrator, object, object, Path]:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        code_provider=code_provider or StubCodeProvider(),
        executor_factory=executor_factory or fake_executor_factory(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    orch.cairn_home.mkdir(parents=True, exist_ok=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True, exist_ok=True)
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    agent_db = orch.agentfs_dir / f"{agent_id}.db"
    agent_ws = await Fsdantic.open(path=str(agent_db))

    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)
    await orch.workspace_cache.put(str(agent_db), agent_ws)

    return orch, bin_ws, agent_ws, agent_db


@pytest.mark.asyncio
async def test_run_agent_transitions_to_reviewing(tmp_path: Path) -> None:
    provider = StubCodeProvider()
    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(tmp_path, provider)

    ctx = AgentContext(
        agent_id="agent-success",
        task="create file",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.REVIEWING
        assert ctx.submission is not None
        assert ctx.submission["summary"] == "ok"

        preview_file = orch.cairn_home / "workspaces" / ctx.agent_id / "generated.txt"
        assert preview_file.read_text(encoding="utf-8") == "from sandbox"

        task_file = orch.cairn_home / "workspaces" / ctx.agent_id / ".cairn" / "task.py"
        assert task_file.read_text(encoding="utf-8") == "x = 1"

        submission_file = orch.cairn_home / "workspaces" / ctx.agent_id / ".cairn" / "submission.json"
        payload = json.loads(submission_file.read_text(encoding="utf-8"))
        assert payload["summary"] == "ok"

        assert provider.reference == ctx.task
        assert provider.context is not None
        assert provider.context["agent_id"] == ctx.agent_id
        from cairn.runtime.driver import ProjectView

        # Providers receive a narrow read-only view, never the writable db.
        assert isinstance(provider.context["workspace"], ProjectView)
        assert provider.context["project_root"] == orch.project_root
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


@pytest.mark.asyncio
async def test_run_agent_sandbox_failure_transitions_to_errored(tmp_path: Path) -> None:
    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(
        tmp_path,
        executor_factory=fake_executor_factory(fail_message="execution failed"),
    )

    ctx = AgentContext(
        agent_id="agent-fail",
        task="explode",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.ERRORED
        assert "execution failed" in (ctx.error or "")
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


@pytest.mark.asyncio
async def test_run_agent_timeout_transitions_to_errored(tmp_path: Path) -> None:
    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(
        tmp_path,
        executor_factory=fake_executor_factory(timeout=True),
    )

    ctx = AgentContext(
        agent_id="agent-timeout",
        task="sleep",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.ERRORED
        assert "timeout" in (ctx.error or "").lower()
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


@pytest.mark.asyncio
async def test_run_agent_provider_validation_failure_transitions_to_errored(tmp_path: Path) -> None:
    provider = StubCodeProvider(is_valid=False, error="provider validation failed")
    calls: list[str] = []

    def _factory(**kwargs: object) -> object:
        calls.append("called")
        raise AssertionError("executor should not be created for invalid provider code")

    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(
        tmp_path,
        provider,
        executor_factory=_factory,
    )

    ctx = AgentContext(
        agent_id="agent-provider-invalid",
        task="bad provider",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.ERRORED
        assert "provider validation failed" in (ctx.error or "")
        assert calls == []
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


@pytest.mark.asyncio
async def test_accept_agent_requires_reviewing_state(tmp_path: Path) -> None:
    agent_id = "agent-accept-invalid"
    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

    ctx = AgentContext(
        agent_id=agent_id,
        task="not ready",
        priority=TaskPriority.NORMAL,
        state=AgentState.EXECUTING,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        with pytest.raises(ValueError, match="reviewing"):
            await orch.accept_agent(agent_id)
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


@pytest.mark.asyncio
async def test_accept_agent_applies_changeset_and_cleans(tmp_path: Path) -> None:
    agent_id = "agent-accept"
    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

    ctx = AgentContext(
        agent_id=agent_id,
        task="accept",
        priority=TaskPriority.NORMAL,
        state=AgentState.REVIEWING,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    # The disposable workspace holds the agent's post-run state; the run
    # record declares what the executor computed.
    workdir = orch.cairn_home / "workspaces" / agent_id
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "notes").mkdir(parents=True, exist_ok=True)
    (workdir / "notes" / "accept.txt").write_text("accepted", encoding="utf-8")
    result = SandboxResult(
        submission={"summary": "ok", "changed_files": ["notes/accept.txt"], "submitted_at": 1.0},
        changes={"written": ["notes/accept.txt"], "deleted": []},
        log="",
        base_hashes={},  # notes/accept.txt was absent at run start
        exit_code=0,
    )
    await orch._record_run(ctx, result)

    try:
        await orch.accept_agent(agent_id)

        # The change lands in the canonical working tree.
        assert (orch.project_root / "notes" / "accept.txt").read_text(encoding="utf-8") == "accepted"
        assert agent_id not in orch.active_agents
        assert (orch.agentfs_dir / f"bin-{agent_id}.db").exists()
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


@pytest.mark.asyncio
async def test_empty_change_claim_is_flagged_as_mismatch(tmp_path: Path) -> None:
    """Review §2.7a: an agent claiming ``changed_files=[]`` while actually
    changing files must be flagged.  The mismatch check previously gated on a
    non-empty claim, so an empty claim was never cross-checked against the
    computed changeset."""
    agent_id = "agent-lie"
    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

    ctx = AgentContext(
        agent_id=agent_id,
        task="lie",
        priority=TaskPriority.NORMAL,
        state=AgentState.EXECUTING,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        result = SandboxResult(
            submission={"summary": "I did nothing", "changed_files": [], "submitted_at": 1.0},
            changes={"written": ["x.py", "y.py"], "deleted": []},
            log="",
            base_hashes={},
            exit_code=0,
        )
        await orch._record_run(ctx, result)

        assert ctx.claim_mismatch is True
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


@pytest.mark.asyncio
async def test_reject_agent_requires_reviewing_state(tmp_path: Path) -> None:
    agent_id = "agent-reject-invalid"
    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

    ctx = AgentContext(
        agent_id=agent_id,
        task="not ready",
        priority=TaskPriority.NORMAL,
        state=AgentState.SUBMITTING,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        with pytest.raises(ValueError, match="reviewing"):
            await orch.reject_agent(agent_id)
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


@pytest.mark.asyncio
async def test_reject_agent_discards_workspace(tmp_path: Path) -> None:
    agent_id = "agent-reject"
    orch, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

    ctx = AgentContext(
        agent_id=agent_id,
        task="reject",
        priority=TaskPriority.NORMAL,
        state=AgentState.REVIEWING,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await agent_ws.files.write("notes/reject.txt", "no")
        await orch.reject_agent(agent_id)

        # Reject discards the disposable workspace; the tree is untouched.
        assert not (orch.cairn_home / "workspaces" / agent_id).exists()
        assert (orch.project_root / "notes" / "reject.txt").exists() is False
        assert agent_id not in orch.active_agents
        assert (orch.agentfs_dir / f"bin-{agent_id}.db").exists()
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)


class _FlakyOrchestratorLifecycle:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.save_calls = 0
        self.records: list[object] = []

    async def load(self, agent_id: str) -> None:
        _ = agent_id

    async def save(self, record: object) -> None:
        self.save_calls += 1
        self.records.append(record)
        if self.failures:
            raise self.failures.pop(0)


@pytest.mark.asyncio
async def test_save_lifecycle_record_retries_recoverable_errors(tmp_path: Path) -> None:
    orch = CairnOrchestrator(project_root=tmp_path / "project", cairn_home=tmp_path / "cairn-home")
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    lifecycle = _FlakyOrchestratorLifecycle([RecoverableError("t1"), RecoverableError("t2")])
    orch.lifecycle = lifecycle

    agent_db_path = tmp_path / "agent-retry-save.db"
    agent_ws = await Fsdantic.open(path=str(agent_db_path))
    ctx = AgentContext(
        agent_id="agent-retry-save",
        task="save with retry",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )

    try:
        await orch._save_lifecycle_record(ctx)
        assert lifecycle.save_calls == 3
    finally:
        await _safe_close(agent_ws)


@pytest.mark.asyncio
async def test_save_lifecycle_record_retry_exhaustion_bubbles_error(tmp_path: Path) -> None:
    orch = CairnOrchestrator(project_root=tmp_path / "project", cairn_home=tmp_path / "cairn-home")
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    lifecycle = _FlakyOrchestratorLifecycle([RecoverableError("t1"), RecoverableError("t2"), RecoverableError("t3")])
    orch.lifecycle = lifecycle

    agent_db_path = tmp_path / "agent-retry-exhausted.db"
    agent_ws = await Fsdantic.open(path=str(agent_db_path))
    ctx = AgentContext(
        agent_id="agent-retry-exhausted",
        task="save retry exhausted",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )

    try:
        with pytest.raises(RecoverableError, match="t3"):
            await orch._save_lifecycle_record(ctx)

        assert lifecycle.save_calls == 3
    finally:
        await _safe_close(agent_ws)
