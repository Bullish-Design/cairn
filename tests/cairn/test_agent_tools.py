"""Tests for the sandbox API (the bootstrap script shipped into the sandbox).

The bootstrap lives in ``cairn.runtime.sandbox.boot`` and is executed with the
workspace path taken from ``CAIRN_WORKSPACE``. These tests load it fresh (via
importlib) with that variable pointed at a tmp directory, exercising the same
code that runs inside bubblewrap.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType

import pytest

from cairn.runtime.sandbox import boot as boot_source


def _load_boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CAIRN_WORKSPACE", str(tmp_path))
    module_name = f"cairn_sandbox_boot_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(module_name, Path(boot_source.__file__))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_read_write_round_trip_and_path_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = _load_boot(tmp_path, monkeypatch)

    assert boot.write_file("notes/todo.txt", "ship it") is True
    assert boot.read_file("notes/todo.txt") == "ship it"
    assert boot.file_exists("notes/todo.txt") is True
    assert boot.file_exists("missing.txt") is False

    assert boot.delete_file("notes/todo.txt") is True
    assert boot.file_exists("notes/todo.txt") is False
    assert boot.delete_file("notes/todo.txt") is False  # already gone

    with pytest.raises(ValueError, match="Absolute paths"):
        boot.write_file("/etc/passwd", "nope")
    with pytest.raises(ValueError, match="traversal"):
        boot.read_file("../outside.txt")
    with pytest.raises(FileNotFoundError):
        boot.read_file("missing.txt")


@pytest.mark.asyncio
async def test_list_dir_and_search_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = _load_boot(tmp_path, monkeypatch)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / "src" / "nested" / "util.py").write_text("y = 2", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("doc", encoding="utf-8")

    assert boot.list_dir("/") == ["docs", "src"]
    assert boot.list_dir("src") == ["main.py", "nested"]
    assert boot.search_files("**/*.py") == ["src/main.py", "src/nested/util.py"]


@pytest.mark.asyncio
async def test_search_content_scopes_and_invalid_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = _load_boot(tmp_path, monkeypatch)
    (tmp_path / "src" / "target.py").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "target.py").write_text("needle", encoding="utf-8")
    (tmp_path / "src" / "nested" / "inner.py").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "nested" / "inner.py").write_text("needle", encoding="utf-8")
    (tmp_path / "docs" / "readme.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "readme.md").write_text("needle", encoding="utf-8")

    global_matches = boot.search_content("needle", path=".")
    assert {match["file"] for match in global_matches} == {"src/target.py", "src/nested/inner.py", "docs/readme.md"}

    scoped = boot.search_content("needle", path="src/**")
    assert {match["file"] for match in scoped} == {"src/target.py", "src/nested/inner.py"}

    dir_scoped = boot.search_content("needle", path="src")
    assert {match["file"] for match in dir_scoped} == {"src/target.py", "src/nested/inner.py"}

    with pytest.raises(ValueError, match="traversal"):
        boot.search_content("needle", path="../outside")
    with pytest.raises(ValueError, match="Absolute paths"):
        boot.search_content("needle", path="/absolute")


@pytest.mark.asyncio
async def test_submit_result_writes_submission_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = _load_boot(tmp_path, monkeypatch)

    assert boot.submit_result("done", ["notes/todo.txt"]) is True
    payload = json.loads((tmp_path / ".cairn" / "submission.json").read_text(encoding="utf-8"))
    assert payload["summary"] == "done"
    assert payload["changed_files"] == ["notes/todo.txt"]
    assert isinstance(payload["submitted_at"], float)


@pytest.mark.asyncio
async def test_run_task_executes_code_with_injected_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = _load_boot(tmp_path, monkeypatch)
    (tmp_path / ".cairn").mkdir(parents=True)
    (tmp_path / ".cairn" / "task.json").write_text(json.dumps({"task_description": "create file"}), encoding="utf-8")
    (tmp_path / ".cairn" / "task.py").write_text(
        'content = read_file("src/main.py")\n'
        'write_file("src/main.py", content + "!")\n'
        'submit_result(summary="done", changed_files=["src/main.py"])\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("hello", encoding="utf-8")

    assert boot._run_task() == 0
    assert boot.read_file("src/main.py") == "hello!"

    payload = json.loads((tmp_path / ".cairn" / "submission.json").read_text(encoding="utf-8"))
    assert payload["summary"] == "done"
    assert payload["changed_files"] == ["src/main.py"]


@pytest.mark.asyncio
async def test_run_task_implicit_submission_when_not_called(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = _load_boot(tmp_path, monkeypatch)
    (tmp_path / ".cairn").mkdir(parents=True)
    (tmp_path / ".cairn" / "task.json").write_text(json.dumps({"task_description": "no submission"}), encoding="utf-8")
    (tmp_path / ".cairn" / "task.py").write_text("x = 1\n", encoding="utf-8")

    assert boot._run_task() == 0
    payload = json.loads((tmp_path / ".cairn" / "submission.json").read_text(encoding="utf-8"))
    assert payload["summary"] == "no submission"
    assert payload["changed_files"] == []


@pytest.mark.asyncio
async def test_run_task_exception_returns_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    boot = _load_boot(tmp_path, monkeypatch)
    (tmp_path / ".cairn").mkdir(parents=True)
    (tmp_path / ".cairn" / "task.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    assert boot.main() == 1
    captured = capsys.readouterr()
    assert "RuntimeError: boom" in captured.err
