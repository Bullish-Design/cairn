"""Adversarial sandbox boundary tests.

The bwrap argv *is* the security model: these tests fail loudly if someone
drops an isolation flag (e.g. ``--unshare-all``) while debugging.  They also
verify the concrete boundary claims: no network, no host home, no writes
outside /workspace, no tty, and the RLIMIT_FSIZE cap.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cairn.runtime.sandbox import BwrapExecutor
from cairn.runtime.settings import ExecutorSettings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("bwrap"), reason="needs bwrap"),
]

BWRAP = os.environ.get("CAIRN_TEST_BWRAP") or os.environ.get("CAIRN_EXECUTOR_BWRAP_PATH") or shutil.which("bwrap")
SANDBOX_PYTHON = os.environ.get("CAIRN_TEST_PYTHON") or os.environ.get("CAIRN_EXECUTOR_PYTHON_PATH")


def _executor(tmp_path: Path, **kwargs: object) -> BwrapExecutor:
    """Minimal executor wired to the bwrap runtime declared by devenv."""
    settings = ExecutorSettings(
        bwrap_path=BWRAP,
        python_path=SANDBOX_PYTHON,
        max_execution_time=30.0,
        **kwargs,
    )
    return BwrapExecutor(
        agent_id="boundary",
        workdir=tmp_path / "work",
        agent_fs=None,  # type: ignore[arg-type]  # not used by the boundary harness
        stable=None,  # type: ignore[arg-type]
        settings=settings,
    )


async def _run_in_sandbox(tmp_path: Path, code: str) -> str:
    """Materialize an empty workspace, run ``code``, and return out.txt content."""
    from fsdantic import Fsdantic

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
    try:
        executor = BwrapExecutor(
            agent_id="boundary",
            workdir=tmp_path / "work",
            agent_fs=agent,
            stable=stable,
            settings=ExecutorSettings(
                bwrap_path=BWRAP,
                python_path=SANDBOX_PYTHON,
                max_execution_time=30.0,
                max_memory_bytes=512 * 1024 * 1024,
            ),
        )
        result = await executor.run(code=code, task="boundary test")
        written = result.changes["written"]
        if "out.txt" not in written:
            return "<missing out.txt>"
        return (await agent.files.read("out.txt")).strip()
    finally:
        await agent.close()
        await stable.close()


BOUNDARY_CASES = [
    ("network", """
        import socket
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=3)
            result = "REACHABLE"
        except OSError:
            result = "blocked"
        write_file("out.txt", result)
    """, "blocked"),

    ("host_home", """
        import os
        write_file("out.txt", "readable" if os.path.isdir(os.path.expanduser("~/.ssh")) else "blocked")
    """, "blocked"),

    ("escape_write", """
        try:
            open("/etc/cairn-escape", "w").write("x")
            result = "WROTE"
        except OSError:
            result = "blocked"
        write_file("out.txt", result)
    """, "blocked"),

    ("tty", """
        import sys
        write_file("out.txt", "TTY" if sys.stdin.isatty() else "blocked")
    """, "blocked"),

    ("fsize", """
        try:
            with open("big.bin", "wb") as fh:
                for _ in range(4096):
                    fh.write(b"x" * (1024 * 1024))
            result = "UNBOUNDED"
        except OSError:
            result = "blocked"
        write_file("out.txt", result)
    """, "blocked"),
]


@pytest.mark.parametrize("name,code,expected", BOUNDARY_CASES, ids=[c[0] for c in BOUNDARY_CASES])
async def test_sandbox_boundary(tmp_path: Path, name: str, code: str, expected: str) -> None:
    result = await _run_in_sandbox(tmp_path, textwrap.dedent(code))
    assert result == expected, f"sandbox boundary '{name}' regressed (got {result!r})"


REQUIRED_FLAGS = {"--unshare-all", "--die-with-parent", "--new-session", "--clearenv"}


def test_argv_contains_required_isolation_flags(tmp_path: Path) -> None:
    """Deleting any isolation flag turns the suite red, no bwrap required."""
    argv = _executor(tmp_path)._build_argv()
    missing = REQUIRED_FLAGS - set(argv)
    assert not missing, f"sandbox isolation flags removed: {missing}"


def test_argv_passes_resource_limit_env(tmp_path: Path) -> None:
    argv = _executor(
        tmp_path,
        max_output_file_bytes=12345,
        max_processes=7,
        max_open_files=99,
    )._build_argv()
    args = list(argv)
    assert "--setenv" in args
    env = {args[i + 1]: args[i + 2] for i in range(len(args) - 2) if args[i] == "--setenv"}
    assert env["CAIRN_MAX_OUTPUT_FILE_BYTES"] == "12345"
    assert env["CAIRN_MAX_PROCESSES"] == "7"
    assert env["CAIRN_MAX_OPEN_FILES"] == "99"


@pytest.mark.skipif(not BWRAP or not SANDBOX_PYTHON, reason="needs bwrap")
@pytest.mark.integration
async def test_fork_bomb_blocked_by_rlimit_nproc(tmp_path: Path) -> None:
    """P3.2: a task forking many processes fails fast instead of hitting the
    machine."""
    from fsdantic import Fsdantic

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
    try:
        executor = BwrapExecutor(
            agent_id="forkbomb",
            workdir=tmp_path / "work",
            agent_fs=agent,
            stable=stable,
            settings=ExecutorSettings(
                bwrap_path=BWRAP,
                python_path=SANDBOX_PYTHON,
                max_execution_time=30.0,
                max_memory_bytes=512 * 1024 * 1024,
                max_processes=32,
            ),
        )
        code = textwrap.dedent("""
            import os, sys
            kids = []
            try:
                for _ in range(200):
                    pid = os.fork()
                    if pid == 0:
                        os._exit(0)
                    kids.append(pid)
                write_file("out.txt", f"forked {len(kids)} ok")
            except BlockingIOError as exc:
                write_file("out.txt", f"blocked: {type(exc).__name__}")
            finally:
                for pid in kids:
                    try:
                        os.waitpid(pid, 0)
                    except OSError:
                        pass
        """)
        result = await executor.run(code=code, task="fork bomb")
        out = (await agent.files.read("out.txt")).strip()
        assert out.startswith("blocked"), out
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.skipif(not BWRAP or not SANDBOX_PYTHON, reason="needs bwrap")
@pytest.mark.integration
async def test_workspace_budget_exceeded(tmp_path: Path) -> None:
    """P3.2: total workspace growth beyond the budget fails the run before
    re-import instead of filling the host disk."""
    from cairn.core.exceptions import ResourceLimitError
    from fsdantic import Fsdantic

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))
    try:
        executor = BwrapExecutor(
            agent_id="budget",
            workdir=tmp_path / "work",
            agent_fs=agent,
            stable=stable,
            settings=ExecutorSettings(
                bwrap_path=BWRAP,
                python_path=SANDBOX_PYTHON,
                max_execution_time=30.0,
                max_memory_bytes=512 * 1024 * 1024,
                max_workspace_bytes=1024 * 1024,
            ),
        )
        code = textwrap.dedent("""
            with open("big.txt", "w") as fh:
                fh.write("x" * (2 * 1024 * 1024))
        """)
        with pytest.raises(ResourceLimitError) as excinfo:
            await executor.run(code=code, task="budget")
        assert excinfo.value.error_code == "WORKSPACE_BUDGET_EXCEEDED"
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.skipif(not BWRAP or not SANDBOX_PYTHON, reason="needs bwrap")
@pytest.mark.integration
async def test_sandbox_tty_unopenable_under_pty(tmp_path: Path) -> None:
    """P3.1: under a real pty, the sandbox still sees no tty and /dev/tty is
    unopenable."""
    import pty as pty_mod

    from fsdantic import Fsdantic

    master_fd, slave_fd = pty_mod.openpty()
    helper = tmp_path / "pty_helper.py"
    helper.write_text(
        textwrap.dedent('''
            import asyncio, os
            from fsdantic import Fsdantic
            from cairn.runtime.sandbox import BwrapExecutor
            from cairn.runtime.settings import ExecutorSettings

            async def main() -> None:
                os.dup2(int(os.environ["PTY_FD"]), 0)
                s = await Fsdantic.open(path=os.environ["STABLE"])
                a = await Fsdantic.open(path=os.environ["AGENT"])
                e = BwrapExecutor(
                    agent_id="p",
                    workdir=os.environ["WORK"],
                    agent_fs=a,
                    stable=s,
                    settings=ExecutorSettings(
                        bwrap_path=os.environ["BWRAP"],
                        python_path=os.environ["PYTHON"],
                        max_execution_time=30.0,
                        max_memory_bytes=536870912,
                    ),
                )
                await e.run(code=os.environ["CODE"], task="pty")
                content = await a.files.read("out.txt")
                with open(os.environ["RESULT"], "w", encoding="utf-8") as fh:
                    fh.write(content)
                await a.close()
                await s.close()

            asyncio.run(main())
        '''),
        encoding="utf-8",
    )

    code = textwrap.dedent("""\
        import sys, os
        try:
            open("/dev/tty", "r").close()
            tty_openable = True
        except OSError:
            tty_openable = False
        write_file("out.txt", f"isatty={sys.stdin.isatty()} tty_openable={tty_openable}")
    """)
    result_path = tmp_path / "result.txt"
    # Run the sandbox with the pty slave as this process's stdin so the
    # test is meaningful (a non-tty stdin would pass trivially).
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(helper),
        env={
            **os.environ,
            "PTY_FD": str(slave_fd),
            "STABLE": str(tmp_path / "stable.db"),
            "AGENT": str(tmp_path / "agent.db"),
            "WORK": str(tmp_path / "work"),
            "RESULT": str(result_path),
            "BWRAP": BWRAP or "",
            "PYTHON": SANDBOX_PYTHON or "",
            "CODE": code,
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        pass_fds=(slave_fd,),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    assert proc.returncode == 0, stderr.decode()
    assert result_path.exists(), "sandbox never wrote out.txt"
    out = result_path.read_text(encoding="utf-8").strip()
    assert out == "isatty=False tty_openable=False", out
    os.close(master_fd)
    os.close(slave_fd)
