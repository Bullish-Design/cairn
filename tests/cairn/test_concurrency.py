"""Tests for Turso concurrency features (WAL + MVCC) in cairn workspace layer.

These tests exercise the concurrency parameters that flow through:
  cairn.open_workspace / WorkspaceManager → fsdantic.Fsdantic.open
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cairn import open_workspace
from cairn.runtime.workspace_manager import WorkspaceManager


# ---------------------------------------------------------------------------
# WAL mode tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWALMode:
    """Verify WAL journal mode is enabled by default and can be disabled."""

    async def test_open_workspace_enables_wal_by_default(self, tmp_path: Path) -> None:
        """open_workspace() should enable WAL mode by default."""
        workspace = await open_workspace(tmp_path / "wal_default.db")
        try:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"
        finally:
            await workspace.close()

    async def test_open_workspace_disable_wal(self, tmp_path: Path) -> None:
        """open_workspace(enable_wal=False) should skip explicit WAL pragma."""
        workspace = await open_workspace(tmp_path / "no_wal.db", enable_wal=False)
        try:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            # Without explicit WAL, turso may default to wal or delete.
            assert result[0] in ("wal", "delete", "memory")
        finally:
            await workspace.close()

    async def test_wal_concurrent_read_while_writing(self, tmp_path: Path) -> None:
        """WAL mode should allow a reader while a writer is active."""
        db_path = tmp_path / "wal_concurrent.db"

        writer = await open_workspace(db_path)
        try:
            await writer.files.write("/file1.txt", "initial")

            reader = await open_workspace(db_path)
            try:
                # Reader sees existing data
                content = await reader.files.read("/file1.txt")
                assert content == "initial"

                # Writer writes more while reader is open
                await writer.files.write("/file2.txt", "added")

                # Reader can see new data (WAL visibility)
                content2 = await reader.files.read("/file2.txt")
                assert content2 == "added"
            finally:
                await reader.close()
        finally:
            await writer.close()


# ---------------------------------------------------------------------------
# MVCC tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMVCCMode:
    """Verify MVCC (BEGIN CONCURRENT) support via enable_mvcc."""

    async def test_open_workspace_with_mvcc(self, tmp_path: Path) -> None:
        """open_workspace(enable_mvcc=True) should open with MVCC + WAL."""
        workspace = await open_workspace(tmp_path / "mvcc.db", enable_mvcc=True)
        try:
            # WAL should be forced on
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"

            # Basic operations work
            await workspace.files.write("/mvcc_test.txt", "mvcc content")
            content = await workspace.files.read("/mvcc_test.txt")
            assert content == "mvcc content"
        finally:
            await workspace.close()

    async def test_mvcc_forces_wal_even_when_disabled(self, tmp_path: Path) -> None:
        """enable_mvcc=True should force WAL on even if enable_wal=False."""
        workspace = await open_workspace(
            tmp_path / "mvcc_wal.db",
            enable_wal=False,
            enable_mvcc=True,
        )
        try:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"
        finally:
            await workspace.close()

    async def test_mvcc_non_conflicting_writes(self, tmp_path: Path) -> None:
        """Two MVCC connections writing to different files should both succeed."""
        db_path = tmp_path / "mvcc_nc.db"

        ws1 = await open_workspace(db_path, enable_mvcc=True)
        try:
            # Initialise the schema
            await ws1.files.write("/init.txt", "init")

            ws2 = await open_workspace(db_path, enable_mvcc=True)
            try:
                await ws1.files.write("/from_ws1.txt", "ws1 data")
                await ws2.files.write("/from_ws2.txt", "ws2 data")

                # Each connection sees its own writes
                assert await ws1.files.read("/from_ws1.txt") == "ws1 data"
                assert await ws2.files.read("/from_ws2.txt") == "ws2 data"
            finally:
                await ws2.close()
        finally:
            await ws1.close()


# ---------------------------------------------------------------------------
# WorkspaceManager.create_workspace tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateWorkspace:
    """Tests for the non-context-manager create_workspace() method."""

    async def test_create_workspace_returns_tracked_workspace(self, tmp_path: Path) -> None:
        """create_workspace should open, track, and return a workspace."""
        manager = WorkspaceManager()
        workspace = await manager.create_workspace(tmp_path / "create.db")

        try:
            # It should be tracked
            assert workspace in manager._active_workspaces

            # It should be functional
            await workspace.files.write("/test.txt", "hello")
            content = await workspace.files.read("/test.txt")
            assert content == "hello"
        finally:
            await manager.close_all()

        # After close_all, tracking should be empty
        assert len(manager._active_workspaces) == 0

    async def test_create_workspace_with_wal(self, tmp_path: Path) -> None:
        """create_workspace should pass enable_wal through to fsdantic."""
        manager = WorkspaceManager()
        workspace = await manager.create_workspace(
            tmp_path / "create_wal.db",
            enable_wal=True,
        )
        try:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"
        finally:
            await manager.close_all()

    async def test_create_workspace_with_mvcc(self, tmp_path: Path) -> None:
        """create_workspace should pass enable_mvcc through to fsdantic."""
        manager = WorkspaceManager()
        workspace = await manager.create_workspace(
            tmp_path / "create_mvcc.db",
            enable_mvcc=True,
        )
        try:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"

            await workspace.files.write("/test.txt", "mvcc via manager")
            assert await workspace.files.read("/test.txt") == "mvcc via manager"
        finally:
            await manager.close_all()


# ---------------------------------------------------------------------------
# WorkspaceManager.open_workspace passthrough tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestManagerOpenWorkspace:
    """Verify the context manager variant passes concurrency params through."""

    async def test_open_workspace_default_wal(self, tmp_path: Path) -> None:
        """Manager.open_workspace() should enable WAL by default."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "mgr_wal.db") as workspace:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"

    async def test_open_workspace_mvcc_passthrough(self, tmp_path: Path) -> None:
        """Manager.open_workspace(enable_mvcc=True) should work."""
        manager = WorkspaceManager()
        async with manager.open_workspace(
            tmp_path / "mgr_mvcc.db",
            enable_mvcc=True,
        ) as workspace:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"

            await workspace.files.write("/managed.txt", "managed mvcc")
            assert await workspace.files.read("/managed.txt") == "managed mvcc"
