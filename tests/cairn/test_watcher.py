from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import Fsdantic
from watchfiles import Change

from cairn.watcher.watcher import FileWatcher, ProjectFilter


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

        async def fake_awatch(root: Path, watch_filter=None):
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


@pytest.mark.asyncio
async def test_filter_excludes_build_dirs(tmp_path: Path) -> None:
    """P1.1: DefaultFilter plus Cairn exclusions keeps junk out of scope."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    f = ProjectFilter(project_root)

    # Build/vendor/cache junk must be excluded.
    assert f.allows(project_root / ".venv/lib/x.py") is False
    assert f.allows(project_root / ".devenv/state/venv/bin/python") is False
    assert f.allows(project_root / "build/out.bin") is False
    assert f.allows(project_root / "dist/app.js") is False
    assert f.allows(project_root / "target/debug/app") is False
    assert f.allows(project_root / "node_modules/pkg/index.js") is False
    assert f.allows(project_root / ".pytest_cache/v/cache/nodeids") is False
    assert f.allows(project_root / ".ruff_cache/x") is False
    assert f.allows(project_root / ".agentfs/stable.db") is False

    # Database files and sqlite artifacts must be excluded.
    assert f.allows(project_root / "foo.db-wal") is False
    assert f.allows(project_root / "foo.db-shm") is False
    assert f.allows(project_root / "data.sqlite3") is False
    assert f.allows(project_root / "libfoo.so") is False

    # A project living under a directory named "build" must not be excluded wholesale.
    nested_root = tmp_path / "build" / "project"
    f2 = ProjectFilter(nested_root)
    assert f2.allows(nested_root / "src/main.py") is True

    # Real source must be allowed.
    assert f.allows(project_root / "src/main.py") is True
    assert f.allows(project_root / "pyproject.toml") is True


@pytest.mark.asyncio
async def test_watcher_skips_oversized_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    try:
        watcher = FileWatcher(project_root=project_root, workspace=workspace, max_file_bytes=100)

        big = project_root / "big.bin"
        big.write_bytes(b"x" * 200)
        small = project_root / "small.txt"
        small.write_text("ok", encoding="utf-8")

        async def fake_awatch(root: Path, watch_filter=None):
            yield {(Change.added, str(big)), (Change.added, str(small))}

        monkeypatch.setattr("cairn.watcher.watcher.awatch", fake_awatch)

        await watcher.watch()

        assert await workspace.files.exists("big.bin") is False
        assert await workspace.files.read("small.txt") == "ok"
    finally:
        await workspace.close()
