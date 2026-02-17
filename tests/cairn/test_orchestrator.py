from __future__ import annotations

import json
from pathlib import Path

import grail
import pytest
from fsdantic import Fsdantic

from cairn.runtime.agent import AgentContext, AgentState
from cairn.orchestrator.lifecycle import LifecycleStore
from cairn.orchestrator.orchestrator import CairnOrchestrator, _load_grail_script
from cairn.core.exceptions import RecoverableError
from cairn.orchestrator.queue import TaskPriority


async def _safe_close(workspace: object) -> None:
    close_method = getattr(workspace, "close", None)
    if close_method is None:
        return
    try:
        await close_method()
    except Exception:
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


class CheckResult:
    def __init__(self, valid: bool, errors: list[str] | None = None) -> None:
        self.valid = valid
        self.errors = errors or []


class SuccessfulScript:
    def check(self) -> CheckResult:
        return CheckResult(True)

    async def run(self, *, inputs: dict, externals: dict[str, object]) -> None:
        tools = externals
        assert inputs["task_description"] == "create file"
        await tools["write_file"]("generated.txt", "from grail")
        await tools["submit_result"]("ok", ["generated.txt"])


class FailingScript:
    def check(self) -> CheckResult:
        return CheckResult(True)

    async def run(self, *, inputs: dict, externals: dict[str, object]) -> None:
        _ = inputs
        _ = externals
        raise grail.ExecutionError("execution failed")


class InvalidScript:
    def check(self) -> CheckResult:
        return CheckResult(False, ["invalid code"])

    async def run(self, *, inputs: dict, externals: dict[str, object]) -> None:
        raise AssertionError("run should not be called")


def test_load_grail_script_uses_legacy_loader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def _legacy_loader(path: str) -> object:
        calls.append(path)
        return SuccessfulScript()

    monkeypatch.setattr(grail, "load", _legacy_loader, raising=False)

    pym_path = tmp_path / "legacy-task.pym"
    pym_path.write_text("x = 1", encoding="utf-8")

    script = _load_grail_script(pym_path)

    assert isinstance(script, SuccessfulScript)
    assert calls == [str(pym_path)]


def test_load_grail_script_uses_modern_loader_when_legacy_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ScriptLoader:
        @classmethod
        def from_file(cls, path: str) -> object:
            return {"path": path, "loader": cls.__name__}

    monkeypatch.delattr(grail, "load", raising=False)
    monkeypatch.setattr(grail, "Script", ScriptLoader, raising=False)

    pym_path = tmp_path / "modern-task.pym"
    pym_path.write_text("x = 1", encoding="utf-8")

    script = _load_grail_script(pym_path)

    assert script == {"path": str(pym_path), "loader": "ScriptLoader"}


def test_load_grail_script_raises_when_no_loader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delattr(grail, "load", raising=False)
    monkeypatch.delattr(grail, "Script", raising=False)
    monkeypatch.delattr(grail, "Program", raising=False)

    pym_path = tmp_path / "missing-loader-task.pym"
    pym_path.write_text("x = 1", encoding="utf-8")

    with pytest.raises(RuntimeError, match="No supported Grail script loader found"):
        _load_grail_script(pym_path)


async def _setup_orchestrator(
    tmp_path: Path, code_provider: StubCodeProvider | None = None
) -> tuple[CairnOrchestrator, object, object, object, Path]:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        code_provider=code_provider or StubCodeProvider(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    orch.cairn_home.mkdir(parents=True, exist_ok=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True, exist_ok=True)
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    agent_db_path = tmp_path / "agent.db"
    agent_ws = await Fsdantic.open(path=str(agent_db_path))

    orch.stable = stable
    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)
    await orch.workspace_cache.put(str(agent_db_path), agent_ws)

    return orch, stable, bin_ws, agent_ws, agent_db_path


async def _setup_orchestrator_with_agent_db(
    tmp_path: Path,
    agent_id: str,
    code_provider: StubCodeProvider | None = None,
) -> tuple[CairnOrchestrator, object, object, object, Path]:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        code_provider=code_provider or StubCodeProvider(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    orch.cairn_home.mkdir(parents=True, exist_ok=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True, exist_ok=True)
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    agent_db = orch.agentfs_dir / f"{agent_id}.db"
    agent_ws = await Fsdantic.open(path=str(agent_db))

    orch.stable = stable
    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)
    await orch.workspace_cache.put(str(agent_db), agent_ws)

    return orch, stable, bin_ws, agent_ws, agent_db


@pytest.mark.asyncio
async def test_run_agent_transitions_to_reviewing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = StubCodeProvider()
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(tmp_path, provider)

    monkeypatch.setattr("cairn.orchestrator.orchestrator._load_grail_script", lambda _: SuccessfulScript())

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
        assert preview_file.read_text(encoding="utf-8") == "from grail"

        pym_file = orch.project_root / ".grail" / "agents" / ctx.agent_id / "task.pym"
        assert pym_file.read_text(encoding="utf-8") == "x = 1"

        check_file = orch.project_root / ".grail" / "agents" / ctx.agent_id / "check.json"
        assert json.loads(check_file.read_text(encoding="utf-8")) == {"errors": [], "valid": True}

        assert provider.reference == ctx.task
        assert provider.context is not None
        assert provider.context["agent_id"] == ctx.agent_id
        assert provider.context["workspace"] is agent_ws
        assert provider.context["stable"] is stable
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_run_agent_transitions_to_errored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(tmp_path)

    monkeypatch.setattr("cairn.orchestrator.orchestrator._load_grail_script", lambda _: FailingScript())

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
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_run_agent_provider_validation_failure_transitions_to_errored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = StubCodeProvider(is_valid=False, error="provider validation failed")
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(tmp_path, provider)

    def _raise(_: str) -> object:
        raise AssertionError("_load_grail_script should not be called")

    monkeypatch.setattr("cairn.orchestrator.orchestrator._load_grail_script", _raise)

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
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_run_agent_validation_failure_transitions_to_errored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(tmp_path)

    monkeypatch.setattr("cairn.orchestrator.orchestrator._load_grail_script", lambda _: InvalidScript())

    ctx = AgentContext(
        agent_id="agent-invalid",
        task="bad code",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.ERRORED
        assert "Grail validation failed" in (ctx.error or "")
        assert "invalid code" in (ctx.error or "")

        check_file = orch.project_root / ".grail" / "agents" / ctx.agent_id / "check.json"
        assert json.loads(check_file.read_text(encoding="utf-8")) == {"errors": ["invalid code"], "valid": False}
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_accept_agent_requires_reviewing_state(tmp_path: Path) -> None:
    agent_id = "agent-accept-invalid"
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

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
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_accept_agent_merges_overlay_and_cleans(tmp_path: Path) -> None:
    agent_id = "agent-accept"
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

    ctx = AgentContext(
        agent_id=agent_id,
        task="accept",
        priority=TaskPriority.NORMAL,
        state=AgentState.REVIEWING,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await agent_ws.files.write("notes/accept.txt", "accepted")
        await orch.accept_agent(agent_id)

        assert await stable.files.read("notes/accept.txt") == "accepted"
        assert agent_id not in orch.active_agents
        assert (orch.agentfs_dir / f"bin-{agent_id}.db").exists()
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_reject_agent_requires_reviewing_state(tmp_path: Path) -> None:
    agent_id = "agent-reject-invalid"
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

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
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_reject_agent_discards_overlay(tmp_path: Path) -> None:
    agent_id = "agent-reject"
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator_with_agent_db(tmp_path, agent_id)

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

        assert await stable.files.exists("notes/reject.txt") is False
        assert agent_id not in orch.active_agents
        assert (orch.agentfs_dir / f"bin-{agent_id}.db").exists()
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


class _FlakyOrchestratorLifecycle:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.save_calls = 0
        self.records: list[object] = []

    async def load(self, agent_id: str) -> None:
        _ = agent_id
        return None

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
