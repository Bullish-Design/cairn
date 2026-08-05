"""Tests for the bwrap sandbox executor (BwrapExecutor).

Includes unit tests for change tracking / submission parsing / sandbox argv
(no bwrap needed) and integration tests that exercise the real sandbox when
bubblewrap is available (skipped otherwise).

The executor runs over a disposable real workspace materialized from the
canonical working tree: the computed changeset is the authoritative record of
what the agent did — there is no overlay database and no re-import.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.core.exceptions import TimeoutError as CairnTimeoutError
from cairn.runtime import repo
from cairn.runtime.sandbox import BwrapExecutor, SandboxExecutionError, SandboxResult
from cairn.runtime.settings import ExecutorSettings

BWRAP = os.environ.get("CAIRN_TEST_BWRAP") or os.environ.get("CAIRN_EXECUTOR_BWRAP_PATH") or shutil.which("bwrap")


# ---------------------------------------------------------------------------
# Unit tests (no bwrap required)
# ---------------------------------------------------------------------------


def test_workspace_capture_excludes_scaffolding_and_tracks_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / ".cairn").mkdir()
    (tmp_path / ".cairn" / "task.py").write_text("y", encoding="utf-8")

    manifest = repo.capture_manifest(tmp_path)

    assert set(manifest.entries) == {"src", "src/a.py"}
    assert manifest.entries["src/a.py"].digest == repo.capture_manifest(tmp_path).files()["src/a.py"].digest


def test_diff_detects_changes_adds_deletes_and_symlink_kind(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("b", encoding="utf-8")

    base = repo.capture_manifest(tmp_path)

    (tmp_path / "src" / "a.py").write_text("v2", encoding="utf-8")
    (tmp_path / "src" / "b.py").unlink()
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    (tmp_path / "evil").symlink_to("/etc/passwd")

    diff = repo.diff_manifests(base, repo.capture_manifest(tmp_path))

    assert diff.written == ["evil", "new.txt", "src/a.py"]
    assert diff.removed == ["src/b.py"]
    # The symlink is recorded as a symlink entry; its *target content* is
    # never read (no digest), so host-side reads never dereference it.
    evil = repo.capture_manifest(tmp_path).entries["evil"]
    assert evil.kind == "symlink" and evil.link_target == "/etc/passwd" and evil.digest is None


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
        def __init__(self) -> None:  # skip materialization setup for argv construction
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


def _project_with(tmp_path: Path, files: dict[str, str]) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return project


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_materializes_runs_and_computes_changeset(tmp_path: Path) -> None:
    project = _project_with(tmp_path, {"src/main.py": "hello"})
    settings = _sandbox_settings(tmp_path)
    workdir = tmp_path / "work"
    executor = BwrapExecutor(
        agent_id="agent-x",
        workdir=workdir,
        project_root=project,
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

        # The computed changeset is authoritative and matches the workdir.
        assert sorted(result.changes["written"]) == ["new.txt", "src/main.py"]
        assert result.changes["deleted"] == []
        assert (workdir / "src" / "main.py").read_text(encoding="utf-8") == "hello!"
        assert (workdir / ".cairn" / "task.py").read_text(encoding="utf-8") == code
        assert (workdir / ".cairn" / "run.log").exists()
        # base_hashes covers the touched path that existed at run start.
        assert result.base_hashes["src/main.py"] == repo.capture_manifest(project).files()["src/main.py"].digest

        # The canonical tree is untouched by the run itself.
        assert (project / "src" / "main.py").read_text(encoding="utf-8") == "hello"
    finally:
        pass


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_deletions_recorded_in_changeset(tmp_path: Path) -> None:
    project = _project_with(tmp_path, {"keep.txt": "keep", "overlay.txt": "overlay content"})
    settings = _sandbox_settings(tmp_path)
    executor = BwrapExecutor(
        agent_id="agent-del",
        workdir=tmp_path / "work",
        project_root=project,
        settings=settings,
    )

    code = "delete_file('overlay.txt')\ndelete_file('keep.txt')\nsubmit_result(summary='deleted', changed_files=[])\n"
    try:
        result = await executor.run(code=code, task="delete")
        assert sorted(result.changes["deleted"]) == ["keep.txt", "overlay.txt"]
        assert (tmp_path / "work" / "keep.txt").exists() is False
        assert (tmp_path / "work" / "overlay.txt").exists() is False
        # The canonical tree is untouched by the run itself.
        assert (project / "keep.txt").read_text(encoding="utf-8") == "keep"
    finally:
        pass


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_timeout_kills_process(tmp_path: Path) -> None:
    project = _project_with(tmp_path, {})
    settings = _sandbox_settings(tmp_path, max_execution_time=1.0)
    executor = BwrapExecutor(
        agent_id="agent-slow",
        workdir=tmp_path / "work",
        project_root=project,
        settings=settings,
    )

    code = "import time\ntime.sleep(30)\nsubmit_result(summary='late', changed_files=[])\n"
    with pytest.raises(CairnTimeoutError):
        await executor.run(code=code, task="sleep")


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_failure_reports_traceback(tmp_path: Path) -> None:
    project = _project_with(tmp_path, {})
    settings = _sandbox_settings(tmp_path)
    executor = BwrapExecutor(
        agent_id="agent-boom",
        workdir=tmp_path / "work",
        project_root=project,
        settings=settings,
    )

    code = "raise RuntimeError('boom')\n"
    with pytest.raises(SandboxExecutionError) as exc_info:
        await executor.run(code=code, task="boom")
    assert "boom" in str(exc_info.value)
    assert exc_info.value.error_code == "SANDBOX_EXECUTION_FAILED"


@pytest.mark.skipif(
    not BWRAP or not SANDBOX_PYTHON,
    reason="bwrap or a Nix-store python not available (set CAIRN_TEST_BWRAP / CAIRN_TEST_PYTHON)",
)
@pytest.mark.integration
async def test_real_sandbox_imports_work_stdlib_only(tmp_path: Path) -> None:
    project = _project_with(tmp_path, {})
    settings = _sandbox_settings(tmp_path)
    executor = BwrapExecutor(
        agent_id="agent-imports",
        workdir=tmp_path / "work",
        project_root=project,
        settings=settings,
    )

    code = (
        "import json, hashlib, pathlib\n"
        "write_file('payload.json', json.dumps({'digest': hashlib.sha256(b'x').hexdigest()}))\n"
        "submit_result(summary='stdlib ok', changed_files=['payload.json'])\n"
    )
    result = await executor.run(code=code, task="stdlib")
    assert result.changes["written"] == ["payload.json"]
    assert (tmp_path / "work" / "payload.json").read_text(encoding="utf-8") == (
        '{"digest": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"}'
    )
