"""End-to-end daemon/CLI integration: the CLI is a thin client.

The daemon owns the databases; the CLI writes signals and reads the lifecycle
store read-only.  This is P1.4's core acceptance test: a spawn/queue issued
from a *separate process* (the CLI) reaches the running daemon and the agent
settles in REVIEWING without restarting the daemon.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from cairn.cli import cli
from cairn.core.exceptions import AgentNotFoundError
from cairn.orchestrator.daemon import read_daemon_pid
from cairn.orchestrator.lifecycle import LifecycleRecord, open_lifecycle_readonly
from cairn.runtime.agent import AgentState

pytestmark = [pytest.mark.integration]


async def _wait_for_pidfile(home: Path, timeout: float = 15.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = read_daemon_pid(home)
        if pid is not None:
            return pid
        await asyncio.sleep(0.05)
    raise AssertionError(f"daemon pidfile never appeared under {home}")


async def _wait_for_state(
    project: Path,
    home: Path,
    timeout: float = 60.0,
    states: set[AgentState] | None = None,
) -> LifecycleRecord:
    wanted = states or {AgentState.REVIEWING}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with open_lifecycle_readonly(home) as store:
                records = await store.list_all()
        except AgentNotFoundError:
            records = []
        for record in records:
            if record.state in wanted:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"no agent reached {[s.value for s in wanted]} within {timeout}s")


def _daemon_env(project: Path, home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CAIRN_PATHS_PROJECT_ROOT"] = str(project)
    env["CAIRN_PATHS_CAIRN_HOME"] = str(home)
    return env


@pytest.mark.integration
async def test_queue_reaches_running_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1.4: 'cairn up' in one process, 'cairn queue' in another; the agent
    reaches REVIEWING via the signal transport without restarting the daemon."""
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "task.py").write_text(
        'write_file("hello.txt", "from the sandbox")\nsubmit_result("did work", ["hello.txt"])\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"

    monkeypatch.setenv("CAIRN_PATHS_PROJECT_ROOT", str(project))
    monkeypatch.setenv("CAIRN_PATHS_CAIRN_HOME", str(home))

    proc = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-m", "cairn.cli.cli", "up"],
        env=_daemon_env(project, home),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await _wait_for_pidfile(home)

        # The CLI (in-process, but via to_thread since main() runs asyncio.run).
        rc = await asyncio.to_thread(cli.main, ["queue", "scripts/task.py"])
        assert rc == 0

        record = await _wait_for_state(project, home, timeout=60)
        assert record.state is AgentState.REVIEWING

        # The agent's output is visible in stable after accept, and the daemon
        # owns stable: an accept signal lands too.
        rc = await asyncio.to_thread(cli.main, ["accept", record.agent_id, "--timeout", "60"])
        assert rc == 0
    finally:
        proc.terminate()
        with suppress(subprocess.TimeoutExpired):
            await asyncio.to_thread(proc.wait, 10)
        if proc.poll() is None:
            proc.kill()
            await asyncio.to_thread(proc.wait)


@pytest.mark.integration
async def test_mutation_refused_without_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1.4: mutating commands refuse cleanly when no daemon is running."""
    project = tmp_path / "project"
    project.mkdir(parents=True)
    home = tmp_path / "home"
    monkeypatch.setenv("CAIRN_PATHS_PROJECT_ROOT", str(project))
    monkeypatch.setenv("CAIRN_PATHS_CAIRN_HOME", str(home))

    rc = await asyncio.to_thread(cli.main, ["queue", "scripts/task.py"])
    assert rc == 2
    # No signal was written.
    assert list((home / "signals").glob("*.json")) == []
