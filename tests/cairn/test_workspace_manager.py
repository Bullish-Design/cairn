from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.core.exceptions import WorkspaceError
from cairn.runtime.workspace_manager import WorkspaceManager


@pytest.mark.asyncio
async def test_workspace_context_manager_success(tmp_path: Path) -> None:
    manager = WorkspaceManager()

    async with manager.open_workspace(tmp_path / "workspace.db") as workspace:
        assert workspace in manager._active_workspaces

    assert len(manager._active_workspaces) == 0


@pytest.mark.asyncio
async def test_workspace_context_manager_exception(tmp_path: Path) -> None:
    manager = WorkspaceManager()

    with pytest.raises(RuntimeError, match="Test error"):
        async with manager.open_workspace(tmp_path / "workspace.db") as workspace:
            assert workspace in manager._active_workspaces
            raise RuntimeError("Test error")

    assert len(manager._active_workspaces) == 0


@pytest.mark.asyncio
async def test_workspace_close_all(tmp_path: Path) -> None:
    manager = WorkspaceManager()

    ws1 = await Fsdantic.open(path=str(tmp_path / "ws1.db"))
    ws2 = await Fsdantic.open(path=str(tmp_path / "ws2.db"))

    manager.track_workspace(ws1)
    manager.track_workspace(ws2)

    await manager.close_all()

    assert len(manager._active_workspaces) == 0


@pytest.mark.asyncio
async def test_workspace_open_invalid_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = WorkspaceManager()

    async def _fail(path: Path | str, *, readonly: bool) -> object:
        _ = path
        _ = readonly
        raise RuntimeError("nope")

    monkeypatch.setattr("cairn.runtime.workspace_manager._open_workspace", _fail)

    with pytest.raises(WorkspaceError, match="Failed to open workspace"):
        async with manager.open_workspace(tmp_path):
            raise AssertionError("open_workspace should fail before yielding")


@pytest.mark.asyncio
async def test_workspace_open_failure_does_not_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = WorkspaceManager()
    calls = {"count": 0}

    async def _fail_once(path: Path | str, *, readonly: bool) -> object:
        _ = path
        _ = readonly
        calls["count"] += 1
        raise ConnectionError("transient")

    monkeypatch.setattr("cairn.runtime.workspace_manager._open_workspace", _fail_once)

    with pytest.raises(WorkspaceError, match="Failed to open workspace"):
        async with manager.open_workspace(tmp_path / "no-retry.db"):
            raise AssertionError("open_workspace should fail before yielding")

    assert calls["count"] == 1
