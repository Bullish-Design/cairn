"""Accept safety: fail-closed base revalidation (review §2.6) and
reversibility (P2.3).

The real Git working tree is the canonical source of truth.  ``accept``
revalidates every touched base entry (including explicit absent states) and
refuses with ``ACCEPT_STALE_BASE`` on any discrepancy — a missing run record
fails the gate closed — then applies the computed changeset to the tree,
snapshotting pre-apply content for ``cairn undo``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.core.exceptions import AgentNotFoundError, WorkspaceMergeError
from cairn.orchestrator.lifecycle import RUN_KEY, LifecycleStore, RunRecord
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.sandbox import SandboxResult

pytestmark = [pytest.mark.integration]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


async def _setup_reviewing_agent(
    tmp_path: Path,
    *,
    project_files: dict[str, str],
    agent_files: dict[str, str],
    written: list[str],
    deleted: list[str] | None = None,
    extra_base_hashes: dict[str, str] | None = None,
) -> tuple[CairnOrchestrator, str, Path, object]:
    """Real project tree + orchestrator + one REVIEWING agent.

    ``project_files`` is the tree the agent saw at run start (its base);
    ``agent_files`` is the post-run state of the agent's disposable
    workspace.  The run record's ``base_hashes`` are computed from the
    project tree, so paths absent from ``project_files`` are treated as
    explicitly absent at run start.
    """
    agent_id = "agent-safe"
    project = tmp_path / "project"
    project.mkdir(parents=True)
    _write_tree(project, project_files)

    orch = CairnOrchestrator(project_root=project, cairn_home=tmp_path / "home")
    orch.agentfs_dir.mkdir(parents=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True)

    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)

    agent_db = orch.agentfs_dir / f"{agent_id}.db"
    agent_ws = await Fsdantic.open(path=str(agent_db))
    await orch.workspace_cache.put(str(agent_db), agent_ws)

    workdir = orch.cairn_home / "workspaces" / agent_id
    workdir.mkdir(parents=True)
    _write_tree(workdir, agent_files)

    ctx = AgentContext(
        agent_id=agent_id,
        task="rewrite a.txt",
        priority=TaskPriority.NORMAL,
        state=AgentState.REVIEWING,
        agent_db_path=agent_db,
        agent_fs=agent_ws,
    )
    orch.active_agents[agent_id] = ctx

    base_hashes = {rel: _sha256(content.encode()) for rel, content in project_files.items()}
    base_hashes.update(extra_base_hashes or {})
    result = SandboxResult(
        submission={"summary": "rewrote files", "changed_files": written, "submitted_at": 1.0},
        changes={"written": written, "deleted": deleted or []},
        log="",
        base_hashes=base_hashes,
        exit_code=0,
    )
    await orch._record_run(ctx, result)
    return orch, agent_id, project, bin_ws


async def _safe_close(*workspaces: object) -> None:
    for ws in workspaces:
        try:
            await ws.close()  # type: ignore[misc]
        except Exception:  # noqa: BLE001, S110 - test cleanup is best-effort
            pass


@pytest.mark.integration
async def test_accept_refused_when_tree_changed(tmp_path: Path) -> None:
    """P2.2: accept is refused when the working tree changed since the agent
    ran; --force overrides and the agent's version wins."""
    orch, agent_id, project, bin_ws = await _setup_reviewing_agent(
        tmp_path,
        project_files={"a.txt": "v1\n"},
        agent_files={"a.txt": "agent version\n"},
        written=["a.txt"],
    )
    try:
        # A human edits the tree after the agent read it.
        (project / "a.txt").write_text("v2\n", encoding="utf-8")

        with pytest.raises(WorkspaceMergeError) as excinfo:
            await orch.accept_agent(agent_id)
        assert excinfo.value.error_code == "ACCEPT_STALE_BASE"
        assert "a.txt" in excinfo.value.context.get("stale_paths", [])

        # The tree is untouched by the refusal.
        assert (project / "a.txt").read_text(encoding="utf-8") == "v2\n"

        # --force accepts and the agent's version wins the overwrite.
        stats = await orch.accept_agent(agent_id, force=True)
        assert stats["files_written"] >= 1
        assert (project / "a.txt").read_text(encoding="utf-8") == "agent version\n"
    finally:
        await _safe_close(bin_ws)


@pytest.mark.integration
async def test_accept_ok_when_tree_unchanged(tmp_path: Path) -> None:
    """P2.2: an unmodified base accepts without --force and lands in the tree."""
    orch, agent_id, project, bin_ws = await _setup_reviewing_agent(
        tmp_path,
        project_files={"a.txt": "v1\n"},
        agent_files={"a.txt": "agent version\n"},
        written=["a.txt"],
    )
    try:
        stats = await orch.accept_agent(agent_id)
        assert stats["files_written"] >= 1
        assert (project / "a.txt").read_text(encoding="utf-8") == "agent version\n"
    finally:
        await _safe_close(bin_ws)


@pytest.mark.integration
async def test_undo_restores_pre_accept_state(tmp_path: Path) -> None:
    """P2.3: accept then undo is a no-op on the tree (byte-identical)."""
    orch, agent_id, project, bin_ws = await _setup_reviewing_agent(
        tmp_path,
        project_files={"a.txt": "v1\n"},
        agent_files={"a.txt": "agent version\n"},
        written=["a.txt"],
    )
    try:
        await orch.accept_agent(agent_id)
        assert (project / "a.txt").read_text(encoding="utf-8") == "agent version\n"

        stats = await orch.undo_accept(agent_id)
        assert stats == {"restored": 1, "deleted": 0}
        assert (project / "a.txt").read_text(encoding="utf-8") == "v1\n"
    finally:
        await _safe_close(bin_ws)


@pytest.mark.integration
async def test_undo_after_force_accept_restores_concurrent_edit(tmp_path: Path) -> None:
    """P2.3: undo restores the exact pre-accept state even when a forced accept
    overwrote a concurrent edit."""
    orch, agent_id, project, bin_ws = await _setup_reviewing_agent(
        tmp_path,
        project_files={"a.txt": "v1\n"},
        agent_files={"a.txt": "agent version\n"},
        written=["a.txt"],
    )
    try:
        (project / "a.txt").write_text("v2\n", encoding="utf-8")
        await orch.accept_agent(agent_id, force=True)
        assert (project / "a.txt").read_text(encoding="utf-8") == "agent version\n"

        stats = await orch.undo_accept(agent_id)
        assert stats == {"restored": 1, "deleted": 0}
        # The pre-accept content (the concurrent edit) is back.
        assert (project / "a.txt").read_text(encoding="utf-8") == "v2\n"
    finally:
        await _safe_close(bin_ws)


@pytest.mark.integration
async def test_undo_missing_record_raises(tmp_path: Path) -> None:
    """P2.3: undo for an agent with no snapshot raises UNDO_NOT_FOUND."""
    orch = CairnOrchestrator(project_root=tmp_path / "project", cairn_home=tmp_path / "home")
    orch.agentfs_dir.mkdir(parents=True)
    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    orch.bin = bin_ws
    try:
        with pytest.raises(AgentNotFoundError):
            await orch.undo_accept("agent-never-accepted")
    finally:
        await _safe_close(bin_ws)


@pytest.mark.integration
async def test_accept_refused_when_base_path_deleted(tmp_path: Path) -> None:
    """Review §2.6: a human deleting a file the agent rewrote must fail the
    gate closed (delete/write collision), not silently resurrect the file."""
    orch, agent_id, project, bin_ws = await _setup_reviewing_agent(
        tmp_path,
        project_files={"a.txt": "v1\n"},
        agent_files={"a.txt": "agent version\n"},
        written=["a.txt"],
    )
    try:
        # Human deletes the file while the agent is in review.
        (project / "a.txt").unlink()

        with pytest.raises(WorkspaceMergeError) as excinfo:
            await orch.accept_agent(agent_id)
        assert excinfo.value.error_code == "ACCEPT_STALE_BASE"
        assert "a.txt" in excinfo.value.context.get("stale_paths", [])

        # The refusal must leave the tree untouched.
        assert (project / "a.txt").exists() is False
    finally:
        await _safe_close(bin_ws)


@pytest.mark.integration
async def test_accept_refused_when_path_created_after_agent(tmp_path: Path) -> None:
    """Review §2.6: a human creating a path the agent also created must fail
    the gate closed (create/create collision), not overwrite the human's file."""
    orch, agent_id, project, bin_ws = await _setup_reviewing_agent(
        tmp_path,
        project_files={"a.txt": "v1\n"},
        agent_files={"a.txt": "agent version\n", "new.txt": "agent created\n"},
        written=["a.txt", "new.txt"],  # new.txt was absent at run start
    )
    try:
        # Human creates the same path while the agent was running.
        (project / "new.txt").write_text("human created\n", encoding="utf-8")

        with pytest.raises(WorkspaceMergeError) as excinfo:
            await orch.accept_agent(agent_id)
        assert excinfo.value.error_code == "ACCEPT_STALE_BASE"
        assert "new.txt" in excinfo.value.context.get("stale_paths", [])

        # The refusal must leave the human's file untouched.
        assert (project / "new.txt").read_text(encoding="utf-8") == "human created\n"
    finally:
        await _safe_close(bin_ws)


@pytest.mark.integration
async def test_accept_fails_closed_when_run_record_missing(tmp_path: Path) -> None:
    """Review §2.6: acceptance without a run record must fail closed.  The run
    record is the only ground truth for what the agent touched; without it the
    gate cannot revalidate the base and must refuse rather than merge blindly."""
    orch, agent_id, project, bin_ws = await _setup_reviewing_agent(
        tmp_path,
        project_files={"a.txt": "v1\n"},
        agent_files={"a.txt": "agent version\n"},
        written=["a.txt"],
    )
    try:
        ctx = orch.active_agents[agent_id]
        run_repo = ctx.agent_fs.kv.repository(prefix="", model_type=RunRecord)
        await run_repo.delete(RUN_KEY)

        with pytest.raises(WorkspaceMergeError) as excinfo:
            await orch.accept_agent(agent_id)
        assert excinfo.value.error_code == "ACCEPT_STALE_BASE"

        # The tree is untouched by the refusal.
        assert (project / "a.txt").read_text(encoding="utf-8") == "v1\n"
    finally:
        await _safe_close(bin_ws)
