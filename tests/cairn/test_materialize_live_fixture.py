"""End-to-end materialize-to-disk tests using real on-disk fixtures.

These tests seed fsdantic workspaces from the *live fixture tree* at
``tests/fixtures/sample_project`` (a real directory on disk with nested
files), then run the full Cairn storage flow and verify the *materialized
output directory on disk*:

1. stable is seeded from the fixture files;
2. an agent overlay modifies/adds files and tombstone-deletes a stable-only
   fixture file;
3. ``materialize.to_disk`` writes the overlay-on-stable view to a real
   directory — verified byte-for-byte against the fixtures;
4. the accept merge replays the tombstone and the final materialized stable
   tree on disk no longer contains the deleted file.

This exercises the fsdantic 0.7.0 tombstone feature through the exact path
Cairn uses in production (BwrapExecutor materializes to disk, re-imports
tombstones, accept merges them into stable).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import Fsdantic, MergeStrategy

from cairn.orchestrator.lifecycle import LifecycleStore
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.settings import OrchestratorSettings
from cairn.watcher.watcher import FileWatcher


def _fixture_root() -> Path:
    """The live fixture tree on disk (nested dirs, text + JSON payloads)."""
    return Path(__file__).resolve().parents[1] / "fixtures" / "sample_project"


def _expected_fixture_files() -> dict[str, bytes]:
    """{relative_path: bytes} snapshot of the live fixture tree."""
    manifest: dict[str, bytes] = {}
    for path in sorted(_fixture_root().rglob("*")):
        if path.is_file():
            manifest[path.relative_to(_fixture_root()).as_posix()] = path.read_bytes()
    return manifest


async def _seed_from_fixtures(workspace, root: Path | None = None) -> None:
    """Mirror every file in the live fixture tree into the workspace.

    Uses the production initial-sync path so the test helper and the
    orchestrator share one implementation.
    """
    root = root or _fixture_root()
    watcher = FileWatcher(project_root=root, workspace=workspace)
    await watcher.initial_sync()


def _disk_manifest(root: Path) -> dict[str, bytes]:
    """{relative_path: bytes} snapshot of a materialized output directory."""
    manifest: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            manifest[path.relative_to(root).as_posix()] = path.read_bytes()
    return manifest


async def _safe_close(workspace: object) -> None:
    close_method = getattr(workspace, "close", None)
    if close_method is None:
        return
    try:
        await close_method()
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Pure storage-layer flow with live fixtures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMaterializeLiveFixture:
    async def test_materialize_overlay_on_base_to_disk(self, tmp_path: Path) -> None:
        """Seed stable from the real fixture tree, layer an agent overlay on
        top (modify + add + tombstone), materialize to a real directory, and
        verify the on-disk output byte-for-byte."""
        stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
        agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
        try:
            await _seed_from_fixtures(stable)

            # Agent changes: modify an existing fixture file, add a new one,
            # and delete a stable-only fixture file (tombstone intent).
            modified_main = b"def main():\n    return 'agent-version'\n"
            await agent.files.write("src/main.py", modified_main)
            await agent.files.write("src/new_feature.py", b"NEW = True\n")
            await agent.overlay.tombstone("legacy.txt")

            # Materialize to a NEW real directory on disk.
            output = tmp_path / "materialized"
            result = await agent.materialize.to_disk(target_path=output, base=stable, clean=True)
            assert result.files_written >= 5

            expected = _expected_fixture_files()
            expected["src/main.py"] = modified_main
            expected["src/new_feature.py"] = b"NEW = True\n"

            manifest = _disk_manifest(output)
            # Every fixture file plus the agent's additions are on disk.
            assert set(expected) <= set(manifest)
            for rel, content in expected.items():
                assert manifest[rel] == content, f"content mismatch for {rel}"
            # Unchanged fixture files survive byte-for-byte (base copied first).
            assert manifest["data/config.json"] == expected["data/config.json"]
            assert manifest["README.md"] == expected["README.md"]
            # Tombstones apply at merge time, NOT materialize time: the
            # materialized view still contains the stable-only file so the
            # sandbox can see it (and decide whether to delete it again).
            assert manifest["legacy.txt"] == expected["legacy.txt"]

            # The accept merge replays the tombstone: stable loses the file.
            merge = await stable.overlay.merge(agent, strategy=MergeStrategy.OVERWRITE)
            assert merge.tombstones_applied == 1
            assert merge.errors == []
            assert await stable.files.exists("legacy.txt") is False
            assert await stable.files.read("src/main.py") == "def main():\n    return 'agent-version'\n"
            assert await stable.files.read("src/new_feature.py") == "NEW = True\n"
        finally:
            await agent.close()
            await stable.close()

    async def test_materialize_stable_after_accept_omits_deleted_fixture(self, tmp_path: Path) -> None:
        """After accept, materializing *stable* to disk omits the tombstoned
        file — the on-disk tree is exactly the accepted state."""
        stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
        agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
        try:
            await _seed_from_fixtures(stable)
            await agent.overlay.tombstone("legacy.txt")
            await agent.files.write("src/main.py", b"accepted version\n")

            await stable.overlay.merge(agent, strategy=MergeStrategy.OVERWRITE)

            output = tmp_path / "accepted"
            await stable.materialize.to_disk(target_path=output, base=None, clean=True)

            manifest = _disk_manifest(output)
            expected = _expected_fixture_files()
            expected["src/main.py"] = b"accepted version\n"
            del expected["legacy.txt"]  # tombstoned away by the accept merge

            assert set(manifest) == set(expected)
            for rel, content in expected.items():
                assert manifest[rel] == content
        finally:
            await agent.close()
            await stable.close()

    async def test_reimport_then_materialize_roundtrip(self, tmp_path: Path) -> None:
        """The exact sandbox re-import contract: files written by a sandbox
        land in the overlay, deletions become tombstones, and re-materializing
        the overlay shows written changes while a re-merge preserves them."""
        from cairn.runtime.sandbox import BwrapExecutor
        from cairn.runtime.settings import ExecutorSettings

        stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
        agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
        try:
            await _seed_from_fixtures(stable)

            executor = BwrapExecutor(
                agent_id="agent-roundtrip",
                workdir=tmp_path / "work",
                agent_fs=agent,
                stable=stable,
                settings=ExecutorSettings(),
            )
            written = [("src/main.py", b"rewritten by sandbox\n"), ("notes.txt", b"agent note\n")]
            deleted = ["legacy.txt", "README.md"]
            await executor._reimport(written, deleted)

            assert sorted(await agent.overlay.list_tombstones()) == ["/README.md", "/legacy.txt"]

            # Re-materialize the overlay to a fresh directory: written files
            # appear and tombstoned paths are removed from the OVERLAY — but
            # stable-only files still copy through from base, so the sandbox
            # can see them (tombstones only bite at merge time).
            output = tmp_path / "roundtrip"
            await agent.materialize.to_disk(target_path=output, base=stable, clean=True)

            manifest = _disk_manifest(output)
            assert manifest["src/main.py"] == b"rewritten by sandbox\n"
            assert manifest["notes.txt"] == b"agent note\n"
            assert manifest["data/config.json"] == _expected_fixture_files()["data/config.json"]
            assert manifest["legacy.txt"] == _expected_fixture_files()["legacy.txt"]
            assert manifest["README.md"] == _expected_fixture_files()["README.md"]

            # Accept merge keeps everything consistent in stable.
            merge = await stable.overlay.merge(agent, strategy=MergeStrategy.OVERWRITE)
            assert merge.tombstones_applied == 2
            assert merge.errors == []
            assert await stable.files.exists("legacy.txt") is False
            assert await stable.files.exists("README.md") is False
            assert await stable.files.read("notes.txt") == "agent note\n"
        finally:
            await agent.close()
            await stable.close()


# ---------------------------------------------------------------------------
# Orchestrator initial sync (P1.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOrchestratorInitialSync:
    async def test_initialize_seeds_stable_from_project(self, tmp_path: Path) -> None:
        """P1.2: initialize() mirrors the project tree into stable before the
        worker loop starts, so agents see the project rather than an empty
        tree."""
        project = tmp_path / "project"
        project.mkdir(parents=True)
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
        (project / "notes.txt").write_text("hello\n", encoding="utf-8")

        orch = CairnOrchestrator(
            project_root=project,
            cairn_home=tmp_path / "cairn-home",
            config=OrchestratorSettings(),
        )
        await orch.initialize()
        try:
            assert orch.stable is not None
            assert await orch.stable.files.read("src/a.py", mode="binary") == b"A = 1\n"
            assert await orch.stable.files.read("notes.txt", mode="binary") == b"hello\n"
        finally:
            await orch.shutdown()

    async def test_initialize_can_skip_sync(self, tmp_path: Path) -> None:
        """Tests that want an empty stable can opt out of the initial sync."""
        project = tmp_path / "project"
        project.mkdir(parents=True)
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")

        orch = CairnOrchestrator(
            project_root=project,
            cairn_home=tmp_path / "cairn-home",
            config=OrchestratorSettings(sync_project_on_start=False),
        )
        await orch.initialize()
        try:
            assert orch.stable is not None
            assert await orch.stable.files.exists("src/a.py") is False
        finally:
            await orch.shutdown()


# ---------------------------------------------------------------------------
# Orchestrator accept flow with live fixtures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOrchestratorAcceptLiveFixture:
    async def test_accept_agent_applies_tombstones_from_fixture(self, tmp_path: Path) -> None:
        """Full orchestrator accept: an agent whose overlay carries fixture
        edits + a tombstone for a stable-only fixture file is accepted; the
        merge deletes the file from stable and applies the edits."""
        agent_id = "agent-live"
        orch = CairnOrchestrator(
            project_root=tmp_path / "project",
            cairn_home=tmp_path / "cairn-home",
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

        ctx = AgentContext(
            agent_id=agent_id,
            task="accept live fixture",
            priority=TaskPriority.NORMAL,
            state=AgentState.REVIEWING,
            agent_db_path=agent_db,
            agent_fs=agent_ws,
        )
        orch.active_agents[agent_id] = ctx

        try:
            await _seed_from_fixtures(stable)

            # Agent edits a fixture file, adds one, and tombstones a
            # stable-only fixture file (as the sandbox re-import would).
            await agent_ws.files.write("src/main.py", "agent main\n")
            await agent_ws.files.write("src/new_feature.py", "added\n")
            await agent_ws.overlay.tombstone("legacy.txt")

            stats = await orch.accept_agent(agent_id)

            # accept_agent reports the merge statistics.
            assert stats == {"files_merged": 2, "tombstones_applied": 1}

            # Stable reflects every change, including the tombstone.
            assert await stable.files.read("src/main.py") == "agent main\n"
            assert await stable.files.read("src/new_feature.py") == "added\n"
            assert await stable.files.exists("legacy.txt") is False
            # Unchanged fixture files are untouched (read returns text).
            expected_config = _expected_fixture_files()["data/config.json"].decode()
            assert await stable.files.read("data/config.json") == expected_config
            # Agent workspace was trashed to the bin database.
            assert agent_id not in orch.active_agents
            assert (orch.agentfs_dir / f"bin-{agent_id}.db").exists()
        finally:
            await _safe_close(agent_ws)
            await _safe_close(bin_ws)
            await _safe_close(stable)
