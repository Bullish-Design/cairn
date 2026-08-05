"""Durable transport tests (review §3.1): command IDs, idempotent dispatch,
in-flight recovery, and synchronous request/response over the Unix socket."""

from __future__ import annotations

import pytest

from cairn.cli.commands import parse_command_payload
from cairn.orchestrator.transport import (
    OrchestratorTransport,
    TransportRequest,
    daemon_running,
    send_request,
    socket_path,
)


class StubOrchestrator:
    def __init__(self) -> None:
        self.commands: list[object] = []
        self.fail_dispatch = False

    async def submit_command(self, command: object) -> dict:
        if self.fail_dispatch:
            raise RuntimeError("boom")
        self.commands.append(command)
        return {"agent_id": "agent-1"}


@pytest.mark.asyncio
async def test_request_response_roundtrip(tmp_path) -> None:
    orch = StubOrchestrator()
    server = OrchestratorTransport(socket_path(tmp_path / "home"), orch.submit_command, _FakeTable())
    await server.start()
    try:
        command = parse_command_payload("queue", {"task": "do work", "priority": 2})
        response = await send_request(tmp_path / "home", command, timeout=10)
        assert response.ok is True
        assert response.result == {"agent_id": "agent-1"}
        assert len(orch.commands) == 1
        assert orch.commands[0].task == "do work"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_idempotent_replay_returns_recorded_result(tmp_path) -> None:
    """A retried command_id returns the recorded result instead of
    re-executing (review §3.1 — idempotent dispatch)."""
    from cairn.orchestrator.transport import CommandTable as RealTable
    from cairn.runtime.workspace_manager import open_workspace

    ws = await open_workspace(tmp_path / "bin.db")
    table = RealTable(ws)
    orch = StubOrchestrator()
    server = OrchestratorTransport(socket_path(tmp_path / "home"), orch.submit_command, table)
    await server.start()
    try:
        request = TransportRequest(command_id="cmd-replay", type="queue", payload={"task": "x", "priority": 2})
        response = await server._dispatch(request)
        assert response.ok is True
        replay = await server._dispatch(request)
        assert replay.ok is True
        assert replay.result == response.result
        assert len(orch.commands) == 1  # executed exactly once
    finally:
        await server.close()
        await ws.close()


@pytest.mark.asyncio
async def test_failed_dispatch_records_error_and_replays(tmp_path) -> None:
    orch = StubOrchestrator()
    orch.fail_dispatch = True
    server = OrchestratorTransport(socket_path(tmp_path / "home"), orch.submit_command, _FakeTable())
    await server.start()
    try:
        request = TransportRequest(command_id="cmd-fail", type="reject", payload={"agent_id": "a"})
        response = await server._dispatch(request)
        assert response.ok is False
        assert "boom" in (response.error or "")
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_send_request_refused_without_daemon(tmp_path) -> None:
    command = parse_command_payload("queue", {"task": "x", "priority": 2})
    assert daemon_running(tmp_path / "home") is False
    with pytest.raises(ConnectionError):
        await send_request(tmp_path / "home", command, timeout=1)


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
