"""Tests for new public workspace APIs.

Tests for:
- open_workspace() function
- WorkspaceInspector class
- AgentStateManager class
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cairn import open_workspace, WorkspaceInspector, WorkspaceStats, AgentStateManager
from cairn.runtime.workspace_manager import WorkspaceManager


class TestOpenWorkspace:
    """Tests for open_workspace function."""

    @pytest.mark.asyncio
    async def test_open_workspace_returns_workspace(self, tmp_path: Path) -> None:
        """open_workspace should return a Workspace instance."""
        # Create workspace first
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/test.txt", "hello")

        # Now open with public API
        workspace = await open_workspace(tmp_path / "test.db")
        try:
            content = await workspace.files.read("/test.txt")
            assert content == "hello"
        finally:
            await workspace.close()

    @pytest.mark.asyncio
    async def test_open_workspace_readonly(self, tmp_path: Path) -> None:
        """open_workspace readonly mode should work for reads."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/test.txt", "hello")

        workspace = await open_workspace(tmp_path / "test.db", readonly=True)
        try:
            # Read should work
            content = await workspace.files.read("/test.txt")
            assert content == "hello"
        finally:
            await workspace.close()

    @pytest.mark.asyncio
    async def test_open_workspace_creates_new(self, tmp_path: Path) -> None:
        """open_workspace should create a new workspace if it doesn't exist."""
        workspace = await open_workspace(tmp_path / "new.db")
        try:
            await workspace.files.write("/test.txt", "created")
            content = await workspace.files.read("/test.txt")
            assert content == "created"
        finally:
            await workspace.close()


class TestWorkspaceInspector:
    """Tests for WorkspaceInspector."""

    @pytest.mark.asyncio
    async def test_inspector_tree(self, tmp_path: Path) -> None:
        """Inspector should return tree structure."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/src/main.py", "print('hello')")
            await ws.files.write("/src/utils.py", "# utils")
            await ws.files.write("/README.md", "# README")

        async with await WorkspaceInspector.from_path(tmp_path / "test.db") as inspector:
            tree = await inspector.tree("/")
            # Tree should have some structure
            assert isinstance(tree, dict)
            assert "children" in tree or "name" in tree or "path" in tree

    @pytest.mark.asyncio
    async def test_inspector_list_dir(self, tmp_path: Path) -> None:
        """Inspector list_dir should return directory contents."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/a.txt", "aaa")
            await ws.files.write("/b.txt", "bbb")
            await ws.files.write("/dir/c.txt", "ccc")

        async with await WorkspaceInspector.from_path(tmp_path / "test.db") as inspector:
            names = await inspector.list_dir("/")
            assert isinstance(names, list)
            assert "a.txt" in names or len(names) >= 2

    @pytest.mark.asyncio
    async def test_inspector_list_dir_with_stats(self, tmp_path: Path) -> None:
        """Inspector list_dir with stats should return dicts."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/a.txt", "aaa")

        async with await WorkspaceInspector.from_path(tmp_path / "test.db") as inspector:
            entries = await inspector.list_dir("/", include_stats=True)
            assert isinstance(entries, list)
            if entries:
                assert isinstance(entries[0], dict)
                assert "name" in entries[0]

    @pytest.mark.asyncio
    async def test_inspector_read(self, tmp_path: Path) -> None:
        """Inspector read should return file contents."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/test.txt", "test content")

        async with await WorkspaceInspector.from_path(tmp_path / "test.db") as inspector:
            content = await inspector.read("/test.txt")
            assert content == "test content"

    @pytest.mark.asyncio
    async def test_inspector_exists(self, tmp_path: Path) -> None:
        """Inspector exists should check file existence."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/exists.txt", "here")

        async with await WorkspaceInspector.from_path(tmp_path / "test.db") as inspector:
            assert await inspector.exists("/exists.txt") is True
            assert await inspector.exists("/not-exists.txt") is False

    @pytest.mark.asyncio
    async def test_inspector_stats(self, tmp_path: Path) -> None:
        """Inspector should return stats."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/a.txt", "aaa")
            await ws.files.write("/b.txt", "bbbbb")

        async with await WorkspaceInspector.from_path(tmp_path / "test.db") as inspector:
            stats = await inspector.stats()
            assert isinstance(stats, WorkspaceStats)
            assert stats.file_count >= 2
            assert stats.total_bytes >= 8

    @pytest.mark.asyncio
    async def test_inspector_from_existing_workspace(self, tmp_path: Path) -> None:
        """Inspector can be created from existing workspace."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            await ws.files.write("/test.txt", "content")

            # Create inspector from existing workspace (not owned)
            inspector = WorkspaceInspector(ws)
            content = await inspector.read("/test.txt")
            assert content == "content"
            # Inspector doesn't close workspace since it doesn't own it


class TestAgentStateManager:
    """Tests for AgentStateManager."""

    @pytest.mark.asyncio
    async def test_state_get_set(self, tmp_path: Path) -> None:
        """State manager should get and set values."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "test-agent")

            await state.set("key1", "value1")
            result = await state.get("key1")
            assert result == "value1"

    @pytest.mark.asyncio
    async def test_state_get_default(self, tmp_path: Path) -> None:
        """State manager should return default for missing keys."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "test-agent")

            result = await state.get("missing", default="default_value")
            assert result == "default_value"

    @pytest.mark.asyncio
    async def test_state_delete(self, tmp_path: Path) -> None:
        """State manager should delete keys."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "test-agent")

            await state.set("key1", "value1")
            assert await state.get("key1") == "value1"

            await state.delete("key1")
            assert await state.get("key1") is None

    @pytest.mark.asyncio
    async def test_state_increment_turn(self, tmp_path: Path) -> None:
        """State manager should increment turn counter."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "test-agent")

            turn1 = await state.increment_turn()
            turn2 = await state.increment_turn()
            turn3 = await state.get_turn()

            assert turn1 == 1
            assert turn2 == 2
            assert turn3 == 2

    @pytest.mark.asyncio
    async def test_state_increment(self, tmp_path: Path) -> None:
        """State manager should increment arbitrary counters."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "test-agent")

            val1 = await state.increment("counter")
            val2 = await state.increment("counter", amount=5)

            assert val1 == 1
            assert val2 == 6

    @pytest.mark.asyncio
    async def test_state_namespacing(self, tmp_path: Path) -> None:
        """Different agents should have isolated state."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state1 = AgentStateManager(ws, "agent-1")
            state2 = AgentStateManager(ws, "agent-2")

            await state1.set("key", "value1")
            await state2.set("key", "value2")

            assert await state1.get("key") == "value1"
            assert await state2.get("key") == "value2"

    @pytest.mark.asyncio
    async def test_state_typed_models(self, tmp_path: Path) -> None:
        """State manager should handle typed Pydantic models."""
        from pydantic import BaseModel

        class TurnState(BaseModel):
            turn: int
            context: dict[str, str]

        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "test-agent")

            turn_state = TurnState(turn=5, context={"foo": "bar"})
            await state.set_typed("turn_state", turn_state)

            loaded = await state.get_typed("turn_state", TurnState)
            assert loaded is not None
            assert loaded.turn == 5
            assert loaded.context == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_state_touch_and_last_active(self, tmp_path: Path) -> None:
        """State manager should track last_active timestamp."""
        from datetime import datetime, timezone

        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "test-agent")

            # Initially no last_active
            assert await state.get_last_active() is None

            # Touch sets timestamp
            await state.touch()
            last_active = await state.get_last_active()
            assert last_active is not None
            assert isinstance(last_active, datetime)
            # Should be recent (within last minute)
            now = datetime.now(timezone.utc)
            assert (now - last_active).total_seconds() < 60

    @pytest.mark.asyncio
    async def test_state_clear_all(self, tmp_path: Path) -> None:
        """State manager clear_all should remove all agent state."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "test-agent")

            await state.set("key1", "value1")
            await state.set("key2", "value2")
            await state.increment_turn()

            count = await state.clear_all()
            assert count >= 3

            # All values should be gone
            assert await state.get("key1") is None
            assert await state.get("key2") is None
            assert await state.get_turn() == 0

    @pytest.mark.asyncio
    async def test_state_properties(self, tmp_path: Path) -> None:
        """State manager should expose agent_id and prefix."""
        manager = WorkspaceManager()
        async with manager.open_workspace(tmp_path / "test.db") as ws:
            state = AgentStateManager(ws, "my-agent-123")

            assert state.agent_id == "my-agent-123"
            assert state.prefix == "agent:my-agent-123:"
