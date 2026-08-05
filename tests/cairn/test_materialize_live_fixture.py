"""End-to-end workspace tests using real on-disk fixtures.

The actual Git working tree is the canonical source of truth (review §4.2);
``stable.db`` mirroring and overlay tombstones are gone.  These tests run the
real flow against a *copy* of the live fixture tree
(``tests/fixtures/sample_project``):

1. the disposable workspace is materialized from the tree (byte-for-byte);
2. an agent's changeset is computed against the base manifest;
3. accept applies the changeset to the actual working tree (files written,
   deletions honored, untouched files byte-identical).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.repo import capture_manifest, materialize_workspace
from cairn.runtime.sandbox import SandboxResult


def _fixture_root() -> Path:
    """The live fixture tree on disk (nested dirs, text + JSON payloads)."""
    return Path(__file__).resolve().parents[1] / "fixtures" / "sample_project"


def _disk_manifest(root: Path) -> dict[str, bytes]:
    """{relative_path: bytes} snapshot of a directory tree (no symlink follow)."""
    manifest: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            manifest[path.relative_to(root).as_posix()] = path.read_bytes()
    return manifest


def _fixture_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the fixture tree used as the canonical project."""
    project = tmp_path / "project"
    shutil.copytree(_fixture_root(), project, symlinks=True)
    return project


async def _record_agent_changes(
    orch: CairnOrchestrator,
    agent_id: str,
    *,
    written: list[str],
    deleted: list[str] | None = None,
    agent_files: dict[str, str] | None = None,
) -> None:
    """Persist an executor-computed changeset (run record) and lay the
    agent's post-run files into its disposable workspace."""
    ctx = orch.active_agents[agent_id]
    workdir = orch.cairn_home / "workspaces" / agent_id
    workdir.mkdir(parents=True, exist_ok=True)
    for rel, content in (agent_files or {}).items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    result = SandboxResult(
        submission={"summary": "fixture changes", "changed_files": written, "submitted_at": 1.0},
        changes={"written": written, "deleted": deleted or []},
        log="",
        base_hashes={},
        exit_code=0,
    )
    # Base hashes must reflect the project tree at run start.
    base = capture_manifest(orch.project_root)
    result.base_hashes = {
        rel: entry.digest for rel, entry in base.files().items() if rel in (set(written) | set(deleted or []))
    }
    await orch._record_run(ctx, result)


# ---------------------------------------------------------------------------
# Materialization fidelity with live fixtures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMaterializeLiveFixture:
    async def test_materialize_fixture_tree_byte_for_byte(self, tmp_path: Path) -> None:
        """The disposable workspace is a faithful copy of the tree: every
        fixture file byte-identical, no more and no fewer files."""
        project = _fixture_copy(tmp_path)
        expected = _disk_manifest(_fixture_root())

        output = tmp_path / "workspace"
        file_count = materialize_workspace(project, output)

        manifest = _disk_manifest(output)
        assert set(manifest) == set(expected)
        assert file_count == len(expected)
        for rel, content in expected.items():
            assert manifest[rel] == content, f"content mismatch for {rel}"

    async def test_materialize_excludes_gitignored_and_scaffolding(self, tmp_path: Path) -> None:
        """A project carrying a .gitignore and .git/.cairn scaffolding never
        materializes them into the disposable workspace."""
        project = tmp_path / "project"
        project.mkdir(parents=True)
        (project / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
        (project / "secret.txt").write_text("S", encoding="utf-8")
        (project / "ok.txt").write_text("fine", encoding="utf-8")
        for name in (".git", ".cairn"):
            (project / name).mkdir(parents=True)
            (project / name / "internal.txt").write_text("x", encoding="utf-8")

        output = tmp_path / "workspace"
        materialize_workspace(project, output)

        manifest = _disk_manifest(output)
        assert "ok.txt" in manifest
        assert "secret.txt" not in manifest
        assert not any(part in (".git", ".cairn") for part in manifest)


# ---------------------------------------------------------------------------
# Orchestrator accept flow with live fixtures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOrchestratorAcceptLiveFixture:
    async def test_accept_applies_changeset_to_real_tree(self, tmp_path: Path) -> None:
        """Full orchestrator accept: an agent whose changeset edits a fixture
        file, adds one, and deletes one lands exactly in the actual working
        tree; untouched files stay byte-identical."""
        agent_id = "agent-live"
        project = _fixture_copy(tmp_path)
        orch = CairnOrchestrator(project_root=project, cairn_home=tmp_path / "cairn-home")
        (orch.cairn_home / "workspaces").mkdir(parents=True)
        orch.agentfs_dir.mkdir(parents=True)

        agent_db = orch.agentfs_dir / f"{agent_id}.db"
        from fsdantic import Fsdantic

        bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
        agent_ws = await Fsdantic.open(path=str(agent_db))
        orch.bin = bin_ws
        orch.lifecycle = __import__("cairn.orchestrator.lifecycle", fromlist=["LifecycleStore"]).LifecycleStore(bin_ws)
        await orch.workspace_cache.put(str(agent_db), agent_ws)

        ctx = AgentContext(
            agent_id=agent_id,
            task="accept live fixture",
            priority=TaskPriority.NORMAL,
            state=AgentState.REVIEWING,
            agent_db_path=agent_db,
            agent_fs=agent_ws,
        )
        orch.active_agents[agent_id] = ctx

        expected = _disk_manifest(project)
        assert "legacy.txt" in expected
        assert "src/main.py" in expected

        try:
            await _record_agent_changes(
                orch,
                agent_id,
                written=["src/main.py", "src/new_feature.py"],
                deleted=["legacy.txt"],
                agent_files={
                    "src/main.py": "agent main\n",
                    "src/new_feature.py": "added\n",
                },
            )

            stats = await orch.accept_agent(agent_id)

            assert stats == {"files_written": 2, "files_deleted": 1}

            # The canonical tree reflects every change.
            result_manifest = _disk_manifest(project)
            assert (project / "src" / "main.py").read_text(encoding="utf-8") == "agent main\n"
            assert (project / "src" / "new_feature.py").read_text(encoding="utf-8") == "added\n"
            assert (project / "legacy.txt").exists() is False
            # Untouched fixture files are byte-identical.
            for rel in ("data/config.json", "README.md"):
                assert result_manifest[rel] == expected[rel], rel
            # Agent workspace was trashed to the bin database.
            assert agent_id not in orch.active_agents
            assert (orch.agentfs_dir / f"bin-{agent_id}.db").exists()
        finally:
            await agent_ws.close()
            await bin_ws.close()
