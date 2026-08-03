"""Tests for fsdantic 0.7.0 features consumed by Cairn.

Covers the features Cairn now builds on after the fsdantic v0.3.1 -> v0.7.0
bump:

- overlay tombstones (sandbox deletions that survive the accept merge)
- read-only workspace mode (``WORKSPACE_READONLY`` / ``WORKSPACE_NOT_FOUND``)
- ``busy_timeout_ms`` write-lock wait passthrough
- ``max_content_bytes`` write payload caps (``CONTENT_TOO_LARGE``)
- ``KVManager.increment`` atomic counters
- ``Workspace.serialized()`` read-modify-write primitive
- ``include_base`` overlay+base union query/search
- the sandbox re-import path recording tombstones (no bwrap required —
  exercises ``BwrapExecutor._reimport`` directly against real workspaces)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fsdantic import (
    FileManager,
    Fsdantic,
    MergeStrategy,
    SerializationError,
    ViewQuery,
    WorkspaceError,
)

from cairn import open_workspace
from cairn.core.exceptions import WorkspaceError as CairnWorkspaceError
from cairn.runtime.sandbox import BwrapExecutor
from cairn.runtime.settings import ExecutorSettings
from cairn.runtime.workspace_manager import WorkspaceManager


@pytest.fixture
async def workspace(tmp_path: Path):
    """A fresh writable fsdantic workspace for the test."""
    ws = await Fsdantic.open(path=str(tmp_path / "ws.db"))
    try:
        yield ws
    finally:
        await ws.close()


@pytest.fixture
async def stable_workspace(tmp_path: Path):
    """A workspace that plays the role of the stable target for merges."""
    ws = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    try:
        yield ws
    finally:
        await ws.close()


# ---------------------------------------------------------------------------
# Overlay tombstones (fsdantic 0.7.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTombstones:
    """Verify overlay.tombstone semantics cairn relies on for sandbox deletes."""

    async def test_tombstone_removes_overlay_file_and_records_marker(self, workspace) -> None:
        await workspace.files.write("/x.txt", "content")
        assert await workspace.files.exists("/x.txt") is True

        await workspace.overlay.tombstone("/x.txt")

        assert await workspace.files.exists("/x.txt") is False
        assert await workspace.overlay.list_tombstones() == ["/x.txt"]

    async def test_tombstone_stable_only_path_still_records_intent(self, workspace) -> None:
        """A path that exists only in the merge target (stable) can be
        tombstoned: the local removal is tolerated and the intent recorded."""
        await workspace.overlay.tombstone("/legacy.txt")

        assert await workspace.overlay.list_tombstones() == ["/legacy.txt"]

    async def test_tombstone_normalizes_path(self, workspace) -> None:
        await workspace.files.write("/dir/file.txt", "x")

        await workspace.overlay.tombstone("dir//./file.txt")

        assert await workspace.overlay.list_tombstones() == ["/dir/file.txt"]

    async def test_tombstone_directory_removes_recursively(self, workspace) -> None:
        await workspace.files.write("/dir/a.txt", "a")
        await workspace.files.write("/dir/sub/b.txt", "b")

        await workspace.overlay.tombstone("/dir")

        assert await workspace.files.exists("/dir/a.txt") is False
        assert await workspace.files.exists("/dir/sub/b.txt") is False
        assert await workspace.overlay.list_tombstones() == ["/dir"]

    async def test_merge_applies_tombstones_to_stable(self, workspace, stable_workspace) -> None:
        """The accept-merge replays the agent's tombstones against stable."""
        await stable_workspace.files.write("/x.txt", "base")
        await stable_workspace.files.write("/y.txt", "keep")

        await workspace.overlay.tombstone("/x.txt")
        await workspace.files.write("/new.txt", "new")

        result = await stable_workspace.overlay.merge(workspace, strategy=MergeStrategy.OVERWRITE)

        assert result.tombstones_applied == 1
        assert result.files_merged == 1
        assert result.errors == []
        assert await stable_workspace.files.exists("/x.txt") is False
        assert await stable_workspace.files.read("/y.txt") == "keep"
        assert await stable_workspace.files.read("/new.txt") == "new"

    async def test_merge_mixed_overlay_delete_and_stable_delete(self, workspace, stable_workspace) -> None:
        """Both overlay-owned and stable-only deletions are applied on merge."""
        await stable_workspace.files.write("/stable_only.txt", "s")
        await workspace.files.write("/overlay_owned.txt", "o")

        await workspace.overlay.tombstone("/overlay_owned.txt")
        await workspace.overlay.tombstone("/stable_only.txt")

        result = await stable_workspace.overlay.merge(workspace, strategy=MergeStrategy.OVERWRITE)

        assert result.tombstones_applied == 2
        assert result.errors == []
        assert await stable_workspace.files.exists("/stable_only.txt") is False
        assert await stable_workspace.files.exists("/overlay_owned.txt") is False

    async def test_merge_recreated_file_overrides_tombstone(self, workspace, stable_workspace) -> None:
        """A file re-created in the overlay after tombstoning wins: the file
        phase copies it and the marker becomes inert."""
        await stable_workspace.files.write("/x.txt", "base")

        await workspace.files.write("/x.txt", "v1")
        await workspace.overlay.tombstone("/x.txt")
        await workspace.files.write("/x.txt", "v2")  # re-created after tombstone

        result = await stable_workspace.overlay.merge(workspace, strategy=MergeStrategy.OVERWRITE)

        assert result.files_merged == 1
        assert result.tombstones_applied == 0
        assert result.errors == []
        assert await stable_workspace.files.read("/x.txt") == "v2"

    async def test_clear_tombstone_management(self, workspace) -> None:
        await workspace.files.write("/a.txt", "a")
        await workspace.files.write("/b.txt", "b")
        await workspace.overlay.tombstone("/a.txt")
        await workspace.overlay.tombstone("/b.txt")

        await workspace.overlay.clear_tombstone("/a.txt")
        assert await workspace.overlay.list_tombstones() == ["/b.txt"]

        cleared = await workspace.overlay.clear_tombstones()
        assert cleared == 1
        assert await workspace.overlay.list_tombstones() == []

    async def test_tombstone_persists_across_reopen(self, tmp_path) -> None:
        """Tombstone markers are stored in KV, so they survive close/reopen."""
        path = str(tmp_path / "persist.db")
        ws = await Fsdantic.open(path=path)
        await ws.overlay.tombstone("/gone.txt")
        await ws.close()

        reopened = await Fsdantic.open(path=path)
        try:
            assert await reopened.overlay.list_tombstones() == ["/gone.txt"]
        finally:
            await reopened.close()


# ---------------------------------------------------------------------------
# Sandbox re-import tombstones (no bwrap needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSandboxReimportTombstones:
    """Exercises ``BwrapExecutor._reimport`` directly (no bubblewrap) to
    verify the host-side changeset writes and deletions become overlay files
    and tombstones respectively."""

    def _executor(self, tmp_path: Path, agent, stable) -> BwrapExecutor:
        return BwrapExecutor(
            agent_id="agent-reimport",
            workdir=tmp_path / "work",
            agent_fs=agent,
            stable=stable,
            settings=ExecutorSettings(),
        )

    async def test_reimport_writes_and_tombstones(self, tmp_path: Path) -> None:
        stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
        agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
        try:
            await stable.files.write("keep.txt", "keep")
            await agent.files.write("overlay.txt", "overlay content")

            executor = self._executor(tmp_path, agent, stable)
            written = [("src/main.py", b"new bytes"), ("new.txt", b"added by sandbox")]
            deleted = ["overlay.txt", "keep.txt"]

            await executor._reimport(written, deleted)

            # Written files land in the overlay.
            assert await agent.files.read("src/main.py") == "new bytes"
            assert await agent.files.read("new.txt") == "added by sandbox"
            # Deleted paths are removed from the overlay and recorded as
            # tombstones (normalized with a leading slash) — including the
            # stable-only file that never existed in the overlay.
            assert sorted(await agent.overlay.list_tombstones()) == ["/keep.txt", "/overlay.txt"]
            assert await agent.files.exists("overlay.txt") is False
            assert await agent.files.exists("keep.txt") is False
        finally:
            await agent.close()
            await stable.close()

    async def test_reimport_tombstones_survive_accept_merge(self, tmp_path: Path) -> None:
        """The full flow: sandbox re-import tombstones the stable-only file,
        then the accept merge deletes it from stable."""
        stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
        agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
        try:
            await stable.files.write("src/main.py", "original")
            await stable.files.write("legacy.txt", "stable only")

            executor = self._executor(tmp_path, agent, stable)
            written = [("src/main.py", b"rewritten by sandbox")]
            deleted = ["legacy.txt"]
            await executor._reimport(written, deleted)

            result = await stable.overlay.merge(agent, strategy=MergeStrategy.OVERWRITE)

            assert result.files_merged == 1
            assert result.tombstones_applied == 1
            assert result.errors == []
            assert await stable.files.read("src/main.py") == "rewritten by sandbox"
            assert await stable.files.exists("legacy.txt") is False
        finally:
            await agent.close()
            await stable.close()

    async def test_reimport_failure_is_best_effort(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing tombstone (e.g. a workspace closed underneath) is
        logged, not raised — the re-import loop is best-effort."""
        stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
        agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
        try:
            executor = self._executor(tmp_path, agent, stable)

            async def _boom(*_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
                raise RuntimeError("overlay gone")

            monkeypatch.setattr(agent.overlay, "tombstone", _boom)
            # Should not raise.
            await executor._reimport([], ["some.txt"])
        finally:
            await agent.close()
            await stable.close()


# ---------------------------------------------------------------------------
# Read-only workspace mode (fsdantic 0.5.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestReadonlyMode:
    async def test_readonly_reads_work_and_writes_rejected(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ro.db"
        ws = await Fsdantic.open(path=str(db_path))
        await ws.files.write("/readable.txt", "payload")
        await ws.kv.set("k", {"v": 1})
        await ws.close()

        ro = await open_workspace(db_path, readonly=True)
        try:
            assert ro.readonly is True
            assert await ro.files.read("/readable.txt") == "payload"
            assert await ro.kv.get("k") == {"v": 1}

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.files.write("/blocked.txt", "nope")
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.kv.set("k", {"v": 2})
            assert exc_info.value.code == "WORKSPACE_READONLY"
        finally:
            await ro.close()

    async def test_readonly_missing_database_raises_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(CairnWorkspaceError) as exc_info:
            await open_workspace(tmp_path / "does-not-exist.db", readonly=True)
        assert exc_info.value.error_code == "WORKSPACE_NOT_FOUND"

    async def test_readonly_via_workspace_manager(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ro-mgr.db"
        ws = await Fsdantic.open(path=str(db_path))
        await ws.files.write("/seed.txt", "seed")
        await ws.close()

        manager = WorkspaceManager()
        ro = await manager.create_workspace(db_path, readonly=True)
        try:
            assert ro.readonly is True
            assert await ro.files.read("/seed.txt") == "seed"
            with pytest.raises(WorkspaceError):
                await ro.files.write("/blocked.txt", "nope")
        finally:
            await manager.close_all()


# ---------------------------------------------------------------------------
# busy_timeout_ms (fsdantic 0.5.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBusyTimeoutMs:
    async def test_busy_timeout_passthrough_and_pragma(self, tmp_path: Path) -> None:
        workspace = await open_workspace(tmp_path / "busy.db", busy_timeout_ms=1234)
        try:
            assert workspace.busy_timeout_ms == 1234
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA busy_timeout")
            result = await cursor.fetchone()
            assert result[0] == 1234
        finally:
            await workspace.close()

    async def test_busy_timeout_default(self, tmp_path: Path) -> None:
        workspace = await open_workspace(tmp_path / "busy-default.db")
        try:
            assert workspace.busy_timeout_ms == 5000
        finally:
            await workspace.close()

    async def test_busy_timeout_zero_disables_wait(self, tmp_path: Path) -> None:
        workspace = await open_workspace(tmp_path / "busy-zero.db", busy_timeout_ms=0)
        try:
            assert workspace.busy_timeout_ms == 0
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA busy_timeout")
            result = await cursor.fetchone()
            assert result[0] == 0
        finally:
            await workspace.close()

    async def test_manager_busy_timeout_passthrough(self, tmp_path: Path) -> None:
        manager = WorkspaceManager()
        workspace = await manager.create_workspace(tmp_path / "busy-mgr.db", busy_timeout_ms=250)
        try:
            assert workspace.busy_timeout_ms == 250
        finally:
            await manager.close_all()


# ---------------------------------------------------------------------------
# max_content_bytes (fsdantic 0.5.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMaxContentBytes:
    async def test_files_write_under_and_over_cap(self, tmp_path: Path) -> None:
        workspace = await open_workspace(tmp_path / "cap-files.db", max_content_bytes=10)
        try:
            await workspace.files.write("/small.txt", "ok")
            assert await workspace.files.read("/small.txt") == "ok"

            with pytest.raises(WorkspaceError) as exc_info:
                await workspace.files.write("/big.txt", "x" * 20)
            assert exc_info.value.code == "CONTENT_TOO_LARGE"
        finally:
            await workspace.close()

    async def test_kv_set_over_cap(self, tmp_path: Path) -> None:
        workspace = await open_workspace(tmp_path / "cap-kv.db", max_content_bytes=10)
        try:
            await workspace.kv.set("small", "ok")

            with pytest.raises(WorkspaceError) as exc_info:
                await workspace.kv.set("big", "y" * 20)
            assert exc_info.value.code == "CONTENT_TOO_LARGE"
        finally:
            await workspace.close()

    async def test_unbounded_by_default(self, tmp_path: Path) -> None:
        workspace = await open_workspace(tmp_path / "cap-none.db")
        try:
            assert workspace.max_content_bytes is None
            await workspace.files.write("/big.txt", "z" * (1024 * 1024))
            assert len(await workspace.files.read("/big.txt")) == 1024 * 1024
        finally:
            await workspace.close()

    async def test_manager_max_content_bytes_passthrough(self, tmp_path: Path) -> None:
        manager = WorkspaceManager()
        workspace = await manager.create_workspace(tmp_path / "cap-mgr.db", max_content_bytes=5)
        try:
            assert workspace.max_content_bytes == 5
            with pytest.raises(WorkspaceError) as exc_info:
                await workspace.files.write("/too-big.txt", "0123456789")
            assert exc_info.value.code == "CONTENT_TOO_LARGE"
        finally:
            await manager.close_all()


# ---------------------------------------------------------------------------
# KVManager.increment (fsdantic 0.5.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestKVIncrement:
    async def test_increment_creates_at_zero_and_increments(self, workspace) -> None:
        assert await workspace.kv.increment("counter") == 1
        assert await workspace.kv.increment("counter") == 2
        assert await workspace.kv.increment("counter", amount=5) == 7
        assert await workspace.kv.get("counter") == 7

    async def test_increment_rejects_non_numeric_value(self, workspace) -> None:
        await workspace.kv.set("strkey", "not-a-number")
        with pytest.raises(SerializationError):
            await workspace.kv.increment("strkey")


# ---------------------------------------------------------------------------
# Workspace.serialized() (fsdantic 0.5.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSerialized:
    async def test_serialized_serializes_read_modify_write(self, workspace) -> None:
        async with workspace.serialized():
            current = await workspace.kv.get("counter", default=0)
            await workspace.kv.set("counter", current + 1)
        assert await workspace.kv.get("counter") == 1

    async def test_serialized_concurrent_increments_no_lost_updates(self, workspace) -> None:
        async def bump() -> None:
            async with workspace.serialized():
                current = await workspace.kv.get("counter", default=0)
                await asyncio.sleep(0)
                await workspace.kv.set("counter", current + 1)

        await asyncio.gather(*(bump() for _ in range(10)))
        assert await workspace.kv.get("counter") == 10


# ---------------------------------------------------------------------------
# include_base overlay+base union (fsdantic 0.5.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestIncludeBaseUnion:
    async def test_query_include_base_union(self, workspace, stable_workspace) -> None:
        await stable_workspace.files.write("/base_only.txt", "b")
        await stable_workspace.files.write("/shared.txt", "base")
        await workspace.files.write("/overlay_only.txt", "o")
        await workspace.files.write("/shared.txt", "overlay")

        manager = FileManager(workspace.raw, base_fs=stable_workspace.raw)
        query = ViewQuery(path_pattern="**/*", recursive=True, include_content=False)

        union = await manager.query(query, include_base=True)
        union_paths = sorted(entry.path for entry in union)
        assert union_paths == ["/base_only.txt", "/overlay_only.txt", "/shared.txt"]

        # Overlay wins on collisions: shared.txt content is the overlay's.
        content_query = ViewQuery(path_pattern="**/*", recursive=True, include_content=True)
        content_union = await manager.query(content_query, include_base=True)
        shared = next(entry for entry in content_union if entry.path == "/shared.txt")
        assert shared.content == "overlay"

        overlay_only = await manager.query(query, include_base=False)
        assert sorted(entry.path for entry in overlay_only) == ["/overlay_only.txt", "/shared.txt"]

    async def test_search_include_base_union(self, workspace, stable_workspace) -> None:
        await stable_workspace.files.write("/src/base.py", "b")
        await workspace.files.write("/src/overlay.py", "o")

        manager = FileManager(workspace.raw, base_fs=stable_workspace.raw)
        paths = await manager.search("**/*.py", include_base=True)
        assert sorted(paths) == ["/src/base.py", "/src/overlay.py"]


# ---------------------------------------------------------------------------
# Fsdantic.open unified seam smoke test (readonly flag + property access)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOpenSeam:
    async def test_open_exposes_configuration(self, tmp_path: Path) -> None:
        workspace = await open_workspace(
            tmp_path / "seam.db",
            readonly=False,
            busy_timeout_ms=100,
            max_content_bytes=2048,
        )
        try:
            assert workspace.readonly is False
            assert workspace.busy_timeout_ms == 100
            assert workspace.max_content_bytes == 2048
        finally:
            await workspace.close()
