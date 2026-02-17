from __future__ import annotations

import json
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.runtime.agent import AgentContext, AgentState
from cairn.core.exceptions import ProviderError
from cairn.orchestrator.lifecycle import LifecycleStore, SUBMISSION_KEY, SubmissionRecord
from cairn.orchestrator.orchestrator import CairnOrchestrator
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


class CheckResult:
    def __init__(self, valid: bool, errors: list[str] | None = None) -> None:
        self.valid = valid
        self.errors = errors or []


class DummyScript:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.ran = False

    def check(self) -> CheckResult:
        errors = [] if self.valid else ["invalid code"]
        return CheckResult(self.valid, errors)

    async def run(self, *, inputs: dict, externals: dict[str, object]) -> None:
        _ = inputs
        _ = externals
        self.ran = True


async def _setup_orchestrator(
    tmp_path: Path, code_provider: StubCodeProvider | None = None
) -> tuple[CairnOrchestrator, AgentContext, object, object, object]:
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
async def test_validate_code_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    monkeypatch.setattr("cairn.orchestrator.orchestrator._load_grail_script", lambda _: DummyScript())

    try:
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
        script = await orch._validate_code(ctx, "x = 1")

        assert isinstance(script, DummyScript)
        check_file = orch.project_root / ".grail" / "agents" / ctx.agent_id / "check.json"
        assert json.loads(check_file.read_text(encoding="utf-8")) == {"errors": [], "valid": True}
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_execute_script_phase(tmp_path: Path) -> None:
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    try:
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
        script = DummyScript()
        await orch._execute_script(ctx, script)

        assert script.ran is True
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)


@pytest.mark.asyncio
async def test_submit_results_phase(tmp_path: Path) -> None:
    orch, ctx, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    try:
        submission_repo = agent_ws.kv.repository(prefix="", model_type=SubmissionRecord)
        await submission_repo.save(
            SUBMISSION_KEY,
            SubmissionRecord(
                agent_id=ctx.agent_id,
                submission={
                    "summary": "done",
                    "changed_files": ["notes.txt"],
                    "submitted_at": 1.0,
                },
            ),
        )
        await agent_ws.files.write("notes.txt", "hello")

        await orch._submit_results(ctx)

        assert ctx.submission is not None
        assert ctx.submission["summary"] == "done"

        preview_file = orch.cairn_home / "workspaces" / ctx.agent_id / "notes.txt"
        assert preview_file.read_text(encoding="utf-8") == "hello"
    finally:
        await _safe_close(agent_ws)
        await _safe_close(bin_ws)
        await _safe_close(stable)
