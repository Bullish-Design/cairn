"""Tests for the bwrap sandbox executor (BwrapExecutor).

Includes unit tests for change tracking / submission parsing (no bwrap needed)
and integration tests that exercise the real sandbox when bubblewrap is
available (skipped otherwise).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fsdantic import Fsdantic, MergeStrategy

from cairn.core.exceptions import TimeoutError as CairnTimeoutError
from cairn.runtime.sandbox import BwrapExecutor, SandboxExecutionError, SandboxResult
from cairn.runtime.settings import ExecutorSettings

BWRAP = os.environ.get("CAIRN_TEST_BWRAP") or os.environ.get("CAIRN_EXECUTOR_BWRAP_PATH") or shutil.which("bwrap")


# ---------------------------------------------------------------------------
# Unit tests (no bwrap required)
# ---------------------------------------------------------------------------


def test_snapshot_excludes_scaffolding_and_tracks_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / ".cairn").mkdir()
    (tmp_path / ".cairn" / "task.py").write_text("y", encoding="utf-8")

    manifest = BwrapExecutor._snapshot(tmp_path)

    assert set(manifest) == {"src/a.py"}
    assert BwrapExecutor._sha256(tmp_path / "src" / "a.py") in manifest.values()


def test_diff_snapshot_detects_changes_adds_and_deletes(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("b", encoding="utf-8")

    baseline = BwrapExecutor._snapshot(tmp_path)

    (tmp_path / "src" / "a.py").write_text("v2", encoding="utf-8")
    (tmp_path / "src" / "b.py").unlink()
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    (tmp_path / "evil").symlink_to("/etc/passwd")

    written, deleted = BwrapExecutor._diff_snapshot(tmp_path, baseline)

    written_paths = {rel for rel, _ in written}
    assert written_paths == {"src/a.py", "new.txt"}
    assert (tmp_path / "src" / "a.py").read_bytes() in [content for _, content in written]
    assert deleted == ["src/b.py"]
    assert "evil" not in written_paths  # symlinks are never re-imported


def test_read_submission_parses_and_validates(tmp_path: Path) -> None:
    submission = tmp_path / "submission.json"
    submission.write_text(
        json.dumps({"summary": "ok", "changed_files": ["a.py", "b.py"], "submitted_at": 12.5}),
        encoding="utf-8",
    )
    parsed = BwrapExecutor._read_submission(submission, default_summary="fallback")
    assert parsed == {"summary": "ok", "changed_files": ["a.py", "b.py"], "submitted_at": 12.5}

    assert BwrapExecutor._read_submission(tmp_path / "missing.json", default_summary="x") is None

    submission.write_text("not json", encoding="utf-8")
    assert BwrapExecutor._read_submission(submission, default_summary="x") is None

    submission.write_text(json.dumps({"changed_files": [1, 2]}), encoding="utf-8")
    parsed = BwrapExecutor._read_submission(submission, default_summary="fallback")
    assert parsed is not None
    assert parsed["summary"] == "fallback"
    assert parsed["changed_files"] == []
    assert isinstance(parsed["submitted_at"], float)


def test_build_argv_produces_sandbox_command(tmp_path: Path) -> None:
    settings = ExecutorSettings(bwrap_path="/usr/bin/bwrap", python_path="/usr/bin/python3")
    workdir = tmp_path / "work"
    workdir.mkdir()

    class _Executor(BwrapExecutor):
        def __init__(self) -> None:  # skip fsdantic workspaces for argv construction
            self.settings = settings
            self.workdir = workdir
            self.agent_id = "agent-x"

    executor = _Executor()
    argv = executor._build_argv()

    assert argv[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in argv
    assert "--die-with-parent" in argv
    assert "--clearenv" in argv
    assert argv[argv.index("--bind") + 1] == str(workdir)
    assert argv[argv.index("--bind") + 2] == "/workspace"
    assert "/usr/bin/python3" in argv
    assert argv[-1] == "/workspace/.cairn/boot.py"


def test_runtime_binds_use_declared_closure_manifest(tmp_path: Path) -> None:
    """The declarative closure manifest (pkgs.writeClosure) drives the binds."""
    manifest = tmp_path / "closure.txt"
    manifest.write_text(
        "/nix/store/aaaaaaaa-python-3.13\n/nix/store/bbbbbbbb-glibc-2.42\n",
        encoding="utf-8",
    )
    settings = ExecutorSettings(
        bwrap_path="/usr/bin/bwrap",
        python_path="/nix/store/aaaaaaaa-python-3.13/bin/python3",
        sandbox_closure_path=str(manifest),
    )

    class _Executor(BwrapExecutor):
        def __init__(self) -> None:
            self.settings = settings
            self.workdir = tmp_path
            self.agent_id = "agent-x"

    binds = _Executor()._runtime_bind_args()

    assert binds == [
        "--ro-bind",
        "/nix/store/aaaaaaaa-python-3.13",
        "/nix/store/aaaaaaaa-python-3.13",
        "--ro-bind",
        "/nix/store/bbbbbbbb-glibc-2.42",
        "/nix/store/bbbbbbbb-glibc-2.42",
    ]


def test_runtime_binds_fall_back_when_manifest_missing(tmp_path: Path) -> None:
    """Without a manifest, bind /nix/store and the standalone prefix."""
    settings = ExecutorSettings(
        bwrap_path="/usr/bin/bwrap",
        python_path="/usr/bin/python3",
        sandbox_closure_path=str(tmp_path / "missing.txt"),
        runtime_mounts=[],
    )

    class _Executor(BwrapExecutor):
        def __init__(self) -> None:
            self.settings = settings
            self.workdir = tmp_path
            self.agent_id = "agent-x"

    binds = _Executor()._runtime_bind_args()

    assert "--ro-bind-try" in binds
    assert "/nix/store" in binds


# ---------------------------------------------------------------------------
# Integration tests (require bwrap + a sandbox-runnable python)
# ---------------------------------------------------------------------------


def _env_or(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _sandbox_python() -> str | None:
    """Resolve a Nix-store python for the real sandbox tests.

    NixOS-only: the sandbox runtime is declared by devenv (CAIRN_EXECUTOR_*
    env vars) or given explicitly via CAIRN_TEST_PYTHON; failing that, the
    resolved ``sys.executable`` is used when it lives in the Nix store.
    """
    configured = _env_or("CAIRN_TEST_PYTHON", "CAIRN_EXECUTOR_PYTHON_PATH")
    if configured:
        return str(Path(configured).resolve())
    resolved = Path(sys.executable).resolve()
    if "/nix/store" in resolved.parts:
        return str(resolved)
    return None


def _closure_manifest(tmp_path: Path, python: str) -> str | None:
    """Write the store closure of ``python`` to a manifest file (writeClosure format)."""
    nix_store = shutil.which("nix-store")
    if nix_store is None:
        return None
    try:
        result = subprocess.run(
            [nix_store, "-qR", python],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    manifest = tmp_path / "sandbox-closure.txt"
    manifest.write_text(result.stdout, encoding="utf-8")
    return str(manifest)


SANDBOX_PYTHON = _sandbox_python()


def _sandbox_settings(tmp_path: Path, **kwargs: object) -> ExecutorSettings:
    defaults: dict[str, object] = {
        "bwrap_path": BWRAP,
        "python_path": SANDBOX_PYTHON,
        "max_execution_time": 30.0,
        "max_memory_bytes": 512 * 1024 * 1024,
    }
    if SANDBOX_PYTHON is not None:
        closure = _closure_manifest(tmp_path, SANDBOX_PYTHON)
        if closure is not None:
            defaults["sandbox_closure_path"] = closure
    defaults.update(kwargs)
    return ExecutorSettings(**defaults)


async def _open_workspaces(tmp_path: Path) -> tuple[object, object]:
    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
    return stable, agent


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_executes_reimports_and_submits(tmp_path: Path) -> None:
    stable, agent = await _open_workspaces(tmp_path)
    await stable.files.write("src/main.py", "hello")

    settings = _sandbox_settings(tmp_path)
    workdir = tmp_path / "work"
    executor = BwrapExecutor(
        agent_id="agent-x",
        workdir=workdir,
        agent_fs=agent,
        stable=stable,
        settings=settings,
    )

    code = (
        "content = read_file('src/main.py')\n"
        "write_file('src/main.py', content + '!')\n"
        "write_file('new.txt', 'new file')\n"
        "submit_result(summary='done', changed_files=['src/main.py', 'new.txt'])\n"
    )
    try:
        result = await executor.run(code=code, task="update main")
        assert isinstance(result, SandboxResult)
        assert result.submission is not None
        assert result.submission["summary"] == "done"
        assert result.submission["changed_files"] == ["src/main.py", "new.txt"]

        # Overlay has the re-imported changes; stable is untouched.
        assert await agent.files.read("src/main.py") == "hello!"
        assert await agent.files.read("new.txt") == "new file"
        assert await stable.files.read("src/main.py") == "hello"

        # The workdir doubles as the preview and holds the scaffolding.
        assert (workdir / "src" / "main.py").read_text(encoding="utf-8") == "hello!"
        assert (workdir / ".cairn" / "task.py").read_text(encoding="utf-8") == code
        assert (workdir / ".cairn" / "run.log").exists()
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_deletions_tombstoned_in_overlay(tmp_path: Path) -> None:
    stable, agent = await _open_workspaces(tmp_path)
    await stable.files.write("keep.txt", "keep")
    # A pre-existing overlay file is materialized for the sandbox; deleting it
    # must remove it from the overlay (the merge on accept then drops it).
    await agent.files.write("overlay.txt", "overlay content")

    settings = _sandbox_settings(tmp_path)
    executor = BwrapExecutor(
        agent_id="agent-del",
        workdir=tmp_path / "work",
        agent_fs=agent,
        stable=stable,
        settings=settings,
    )

    # Delete BOTH an overlay-owned file and a stable-only file: the sandbox
    # sees them both on the materialized disk, and the re-import records a
    # tombstone for each (fsdantic >= 0.7.0), so stable-only deletions survive
    # into the accept merge.
    code = "delete_file('overlay.txt')\ndelete_file('keep.txt')\nsubmit_result(summary='deleted', changed_files=[])\n"
    try:
        result = await executor.run(code=code, task="delete")
        assert sorted(result.changes["deleted"]) == ["keep.txt", "overlay.txt"]
        assert await agent.files.exists("overlay.txt") is False
        assert await agent.files.exists("keep.txt") is False
        # Both deletions are recorded as tombstones (normalized with a
        # leading slash) in the agent overlay.
        assert sorted(await agent.overlay.list_tombstones()) == ["/keep.txt", "/overlay.txt"]

        # The accept merge replays the tombstones against stable, so the
        # stable-only file is deleted there too (fsdantic >= 0.7.0).
        merge_result = await stable.overlay.merge(agent, strategy=MergeStrategy.OVERWRITE)
        assert merge_result.tombstones_applied == 2
        assert merge_result.errors == []
        assert await stable.files.exists("keep.txt") is False
        assert await stable.files.exists("overlay.txt") is False
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_timeout_kills_process(tmp_path: Path) -> None:
    stable, agent = await _open_workspaces(tmp_path)
    settings = _sandbox_settings(tmp_path, max_execution_time=1.0)
    executor = BwrapExecutor(
        agent_id="agent-slow",
        workdir=tmp_path / "work",
        agent_fs=agent,
        stable=stable,
        settings=settings,
    )

    code = "import time\ntime.sleep(30)\nsubmit_result(summary='late', changed_files=[])\n"
    try:
        with pytest.raises(CairnTimeoutError):
            await executor.run(code=code, task="sleep")
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_failure_reports_traceback(tmp_path: Path) -> None:
    stable, agent = await _open_workspaces(tmp_path)
    settings = _sandbox_settings(tmp_path)
    executor = BwrapExecutor(
        agent_id="agent-boom",
        workdir=tmp_path / "work",
        agent_fs=agent,
        stable=stable,
        settings=settings,
    )

    code = "raise RuntimeError('boom')\n"
    try:
        with pytest.raises(SandboxExecutionError) as exc_info:
            await executor.run(code=code, task="boom")
        assert "boom" in str(exc_info.value)
        assert exc_info.value.error_code == "SANDBOX_EXECUTION_FAILED"
        # Failed runs leave no changes in the overlay.
        assert await agent.files.exists("boom.txt") is False
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_imports_work_stdlib_only(tmp_path: Path) -> None:
    stable, agent = await _open_workspaces(tmp_path)
    settings = _sandbox_settings(tmp_path)
    executor = BwrapExecutor(
        agent_id="agent-imports",
        workdir=tmp_path / "work",
        agent_fs=agent,
        stable=stable,
        settings=settings,
    )

    code = (
        "import json, hashlib, pathlib\n"
        "write_file('payload.json', json.dumps({'digest': hashlib.sha256(b'x').hexdigest()}))\n"
        "submit_result(summary='stdlib ok', changed_files=['payload.json'])\n"
    )
    try:
        await executor.run(code=code, task="stdlib")
        assert (
            await agent.files.read("payload.json")
            == '{"digest": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"}'
        )
    finally:
        await agent.close()
        await stable.close()
