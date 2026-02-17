from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import Fsdantic
from watchfiles import Change

from cairn.watcher.watcher import FileWatcher


@pytest.mark.asyncio
async def test_watcher_syncs_file_changes_into_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    try:
        watcher = FileWatcher(project_root=project_root, workspace=workspace)

        created = project_root / "docs" / "note.txt"
        created.parent.mkdir(parents=True, exist_ok=True)
        created.write_text("hello", encoding="utf-8")

        deleted = project_root / "docs" / "old.txt"
        deleted.write_text("bye", encoding="utf-8")
        await workspace.files.write("docs/old.txt", "bye")
        deleted.unlink()

        async def fake_awatch(root: Path):
            assert root == project_root
            yield {
                (Change.added, str(created)),
                (Change.deleted, str(deleted)),
            }

        monkeypatch.setattr("cairn.watcher.watcher.awatch", fake_awatch)

        await watcher.watch()

        assert await workspace.files.read("docs/note.txt") == "hello"
        assert await workspace.files.exists("docs/old.txt") is False
    finally:
        await workspace.close()
