"""Iterative driver + narrow provider view tests (review §4.3, §3.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cairn.core.exceptions import SecurityError
from cairn.runtime.driver import (
    DriverStep,
    ProjectView,
    ScriptedDriver,
    WorkspaceCapability,
)


@pytest.mark.asyncio
async def test_capability_is_confined_to_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    cap = WorkspaceCapability(root)

    assert await cap.read("a.txt") == "hello"
    await cap.write("new.txt", "x")
    assert (root / "new.txt").read_text(encoding="utf-8") == "x"
    assert await cap.list_dir() == ["a.txt", "new.txt"]
    assert await cap.search("*.txt") == ["a.txt", "new.txt"]

    # Traversal and absolute paths are rejected; host files are unreachable.
    for bad in ("../escape", "/etc/passwd", "a/../../b"):
        with pytest.raises(SecurityError):
            await cap.read(bad)
        with pytest.raises(SecurityError):
            await cap.write(bad, "x")
    assert not (tmp_path / "escape").exists()


@pytest.mark.asyncio
async def test_capability_run_requires_sandbox_runner(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    cap = WorkspaceCapability(root)
    with pytest.raises(SecurityError, match="no sandbox runner"):
        await cap.run(["echo", "hi"])

    calls: list[list[str]] = []

    async def runner(command: list[str]) -> tuple[int, str]:
        calls.append(command)
        return 0, ""

    cap = WorkspaceCapability(root, runner=runner)
    code, _ = await cap.run(["git", "status"], timeout=5)
    assert code == 0
    assert calls == [["git", "status"]]


@pytest.mark.asyncio
async def test_scripted_driver_iterates_and_submits(tmp_path: Path) -> None:
    """The reference driver walks inspect → edit → test → submit with a step
    limit; its submission reports the files it actually changed."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")

    runner_calls: list[list[str]] = []

    async def runner(command: list[str]) -> tuple[int, str]:
        runner_calls.append(command)
        return 0, ""

    driver = ScriptedDriver(
        [
            DriverStep(action="read", path="src/main.py"),
            DriverStep(action="write", path="src/main.py", content="print(2)\n"),
            DriverStep(action="run", command=["pytest", "-q"]),
            DriverStep(action="write", path="new.txt", content="added"),
            DriverStep(action="submit", path="src/main.py", content="done"),
        ]
    )
    result = await driver.run("do work", WorkspaceCapability(root, runner=runner), step_limit=10)
    assert result["summary"] == "done"
    assert result["changed_files"] == ["new.txt", "src/main.py"]
    assert (root / "src" / "main.py").read_text(encoding="utf-8") == "print(2)\n"
    assert runner_calls == [["pytest", "-q"]]


@pytest.mark.asyncio
async def test_scripted_driver_step_limit(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    driver = ScriptedDriver([DriverStep(action="write", path=f"f{i}.txt", content="x") for i in range(10)])
    result = await driver.run("t", WorkspaceCapability(root), step_limit=3)
    assert len(result["changed_files"]) == 3
    assert sorted(p.name for p in root.iterdir()) == ["f0.txt", "f1.txt", "f2.txt"]


@pytest.mark.asyncio
async def test_project_view_is_read_only_and_gitignore_aware(tmp_path: Path) -> None:
    """Providers receive a read-only view: they can inspect the tree but
    cannot mutate it, and gitignored paths are not visible."""
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (project / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (project / "secret.txt").write_text("S", encoding="utf-8")

    view = ProjectView(project)
    assert await view.read("src/main.py") == "x = 1\n"
    assert "secret.txt" not in await view.search("**/*")
    assert "secret.txt" not in await view.list_dir(".")
    stat_info = await view.stat("src/main.py")
    assert stat_info["kind"] == "file"

    with pytest.raises(SecurityError):
        await view.read("../outside")


@pytest.mark.asyncio
async def test_project_view_has_no_write_methods(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    view = ProjectView(project)
    assert not hasattr(view, "write")
    assert not hasattr(view, "delete")
