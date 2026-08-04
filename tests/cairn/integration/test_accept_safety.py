"""Accept safety: staleness refusal (P2.2) and reversibility (P2.3)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.core.exceptions import WorkspaceMergeError
from cairn.orchestrator.lifecycle import LifecycleStore
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.sandbox import SandboxResult

pytestmark = [pytest.mark.integration]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _setup_reviewing_agent(tmp_path: Path, *, stable_content: bytes) -> tuple[CairnOrchestrator, str, object, object]:
    """Orchestrator + one agent in REVIEWING whose overlay rewrites a.txt.

    The agent's run record claims a base hash equal to ``stable_content``'s
    digest (what the sandbox saw at run start).
    """
    agent_id = "agent-safe"
    orch = CairnOrchestrator(project_root=tmp_path / "project", cairn_home=tmp_path / "home")
    orch.project_root.mkdir(parents=True)
    orch.agentfs_dir.mkdir(parents=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True)

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    await stable.files.write("a.txt", stable_content, mode="binary")
    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    agent_db = orch.agentfs_dir / f"{agent_id}.db"
    agent_ws = await Fsdantic.open(path=str(agent_db))
    await agent_ws.files.write("a.txt", b"agent version\n", mode="binary")

    orch.stable = stable
    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)
    await orch.workspace_cache.put(str(agent_db), agent_ws)

    ctx = AgentContext(
        agent_id=agent_id,
        task="rewrite a.txt",
        priority=TaskPriority.NORMAL,
        state=AgentState.REVIEWING,
        agent_db_path=agent_db,
        agent_fs=agent_ws,
    )
    orch.active_agents[agent_id] = ctx

    result = SandboxResult(
        submission={"summary": "rewrote a", "changed_files": ["a.txt"], "submitted_at": 1.0},
        changes={"written": ["a.txt"], "deleted": []},
        log="",
        base_hashes={"a.txt": _sha256(stable_content)},
        exit_code=0,
    )
    await orch._record_run(ctx, result)
    return orch, agent_id, stable, bin_ws


async def _safe_close(*workspaces: object) -> None:
    for ws in workspaces:
        try:
            await ws.close()  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            pass


@pytest.mark.integration
async def test_accept_refused_when_stable_changed(tmp_path: Path) -> None:
    """P2.2: accept is refused when stable changed since the agent ran; --force
    overrides and the merge proceeds."""
    orch, agent_id, stable, bin_ws = await _setup_reviewing_agent(
        tmp_path, stable_content=b"v1\n"
    )
    try:
        # Something changed stable after the agent read it.
        await stable.files.write("a.txt", b"v2\n", mode="binary")

        with pytest.raises(WorkspaceMergeError) as excinfo:
            await orch.accept_agent(agent_id)
        assert excinfo.value.error_code == "ACCEPT_STALE_BASE"
        assert "a.txt" in excinfo.value.context.get("stale_paths", [])

        # Stable is untouched by the refusal.
        assert await stable.files.read("a.txt", mode="binary") == b"v2\n"

        # --force accepts and the agent's version wins the overwrite.
        stats = await orch.accept_agent(agent_id, force=True)
        assert stats["files_merged"] >= 1
        assert await stable.files.read("a.txt", mode="binary") == b"agent version\n"
    finally:
        await _safe_close(stable, bin_ws)


@pytest.mark.integration
async def test_accept_ok_when_stable_unchanged(tmp_path: Path) -> None:
    """P2.2: an unmodified base accepts without --force."""
    orch, agent_id, stable, bin_ws = await _setup_reviewing_agent(tmp_path, stable_content=b"v1\n")
    try:
        stats = await orch.accept_agent(agent_id)
        assert stats["files_merged"] >= 1
        assert await stable.files.read("a.txt", mode="binary") == b"agent version\n"
    finally:
        await _safe_close(stable, bin_ws)


@pytest.mark.integration
async def test_undo_restores_pre_accept_state(tmp_path: Path) -> None:
    """P2.3: accept then undo is a no-op on stable (byte-identical)."""
    orch, agent_id, stable, bin_ws = await _setup_reviewing_agent(tmp_path, stable_content=b"v1\n")
    try:
        pre_accept = await stable.files.read("a.txt", mode="binary")
        assert pre_accept == b"v1\n"

        await orch.accept_agent(agent_id)
        assert await stable.files.read("a.txt", mode="binary") == b"agent version\n"

        stats = await orch.undo_accept(agent_id)
        assert stats == {"restored": 1, "deleted": 0}
        assert await stable.files.read("a.txt", mode="binary") == b"v1\n"
    finally:
        await _safe_close(stable, bin_ws)


@pytest.mark.integration
async def test_undo_after_force_accept_restores_concurrent_edit(tmp_path: Path) -> None:
    """P2.3: undo restores the exact pre-accept state even when a forced accept
    overwrote a concurrent edit."""
    orch, agent_id, stable, bin_ws = await _setup_reviewing_agent(tmp_path, stable_content=b"v1\n")
    try:
        await stable.files.write("a.txt", b"v2\n", mode="binary")
        await orch.accept_agent(agent_id, force=True)
        assert await stable.files.read("a.txt", mode="binary") == b"agent version\n"

        stats = await orch.undo_accept(agent_id)
        assert stats == {"restored": 1, "deleted": 0}
        # The pre-accept content (the concurrent edit) is back.
        assert await stable.files.read("a.txt", mode="binary") == b"v2\n"
    finally:
        await _safe_close(stable, bin_ws)


@pytest.mark.integration
async def test_undo_missing_record_raises(tmp_path: Path) -> None:
    """P2.3: undo for an agent with no snapshot raises UNDO_NOT_FOUND."""
    orch = CairnOrchestrator(project_root=tmp_path / "project", cairn_home=tmp_path / "home")
    orch.agentfs_dir.mkdir(parents=True)
    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    orch.stable = stable
    orch.bin = bin_ws
    try:
        from cairn.core.exceptions import AgentNotFoundError

        with pytest.raises(AgentNotFoundError):
            await orch.undo_accept("agent-never-accepted")
    finally:
        await _safe_close(bin_ws, stable)
