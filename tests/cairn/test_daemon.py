from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cairn.orchestrator.daemon import (
    pidfile_path,
    read_daemon_pid,
    remove_daemon_pid,
    write_daemon_pid,
)
from cairn.orchestrator.transport import OrchestratorTransport, daemon_running, socket_path


def test_pidfile_path_under_state(tmp_path: Path) -> None:
    assert pidfile_path(tmp_path) == tmp_path / "state" / "orchestrator.pid"


def test_socket_path_under_state(tmp_path: Path) -> None:
    assert socket_path(tmp_path) == tmp_path / "state" / "orchestrator.sock"


def test_read_daemon_pid_absent(tmp_path: Path) -> None:
    assert read_daemon_pid(tmp_path) is None


def test_read_daemon_pid_without_live_socket(tmp_path: Path) -> None:
    """A pidfile alone (no live control socket) is not trusted: ownership is
    the socket, so a stale pidfile (dead process, or leftover after a crash)
    reads as no daemon (review §3.7 — no PID-reuse/check-then-replace races)."""
    path = pidfile_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert read_daemon_pid(tmp_path) is None
    assert daemon_running(tmp_path) is False


@pytest.mark.asyncio
async def test_daemon_running_and_pid_roundtrip(tmp_path: Path) -> None:
    """A live control socket means a daemon is running; the informational
    pidfile is then readable."""
    transport = OrchestratorTransport(
        socket_path(tmp_path),
        handler=_ok_handler,
        command_table=_FakeTable(),
    )
    await transport.start()
    try:
        assert daemon_running(tmp_path) is True
        write_daemon_pid(tmp_path)
        assert read_daemon_pid(tmp_path) == os.getpid()
    finally:
        await transport.close()
        remove_daemon_pid(tmp_path)
    assert daemon_running(tmp_path) is False
    assert read_daemon_pid(tmp_path) is None


@pytest.mark.asyncio
async def test_second_daemon_cannot_bind_live_socket(tmp_path: Path) -> None:
    """Ownership is the socket bind: while one daemon listens, a second bind
    is refused; after close, a fresh daemon can bind."""
    transport = OrchestratorTransport(socket_path(tmp_path), handler=_ok_handler, command_table=_FakeTable())
    await transport.start()
    try:
        second = OrchestratorTransport(socket_path(tmp_path), handler=_ok_handler, command_table=_FakeTable())
        with pytest.raises(RuntimeError, match="already running"):
            await second.start()
    finally:
        await transport.close()

    assert socket_path(tmp_path).exists() is False
    transport2 = OrchestratorTransport(socket_path(tmp_path), handler=_ok_handler, command_table=_FakeTable())
    await transport2.start()
    try:
        assert daemon_running(tmp_path) is True
    finally:
        await transport2.close()


@pytest.mark.asyncio
async def test_stale_socket_file_is_reclaimed(tmp_path: Path) -> None:
    """A socket file left behind by a crashed daemon (no listener) does not
    block a new daemon: it is detected and unlinked before binding."""
    path = socket_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()  # leftover file, nothing listening

    transport = OrchestratorTransport(path, handler=_ok_handler, command_table=_FakeTable())
    await transport.start()
    try:
        assert daemon_running(tmp_path) is True
    finally:
        await transport.close()


async def _ok_handler(command: object) -> dict:
    return {"ok": True}


class _FakeTable:
    async def begin(self, command_id: str, command_type: str) -> bool:
        return True

    async def load(self, command_id: str):
        return None

    async def complete(self, command_id: str, result: dict) -> None:
        return None

    async def fail(self, command_id: str, error: str) -> None:
        return None

    async def list_pending(self):
        return []

    async def recover_inflight(self) -> int:
        return 0
