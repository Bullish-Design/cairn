from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

import cairn.cli.typer_cli as typer_cli
from cairn.cli.commands import CommandResult, CommandType


runner = CliRunner()


class DummyWorkspace:
    async def close(self) -> None:
        return None


class StubOrchestrator:
    def __init__(self) -> None:
        self.submitted: list[Any] = []
        self.stable = DummyWorkspace()
        self.bin = DummyWorkspace()

    async def submit_command(self, command: Any) -> CommandResult:
        self.submitted.append(command)
        payload: dict[str, Any] = {}
        if command.type is CommandType.LIST_AGENTS:
            payload = {
                "agents": {
                    "agent-1": {"state": "queued", "task": "task", "priority": 2},
                }
            }
        elif command.type is CommandType.STATUS:
            payload = {"state": "queued", "task": "task", "error": None, "submission": None}
        return CommandResult(command_type=command.type, agent_id=getattr(command, "agent_id", None), payload=payload)


def _patch_orchestrator(monkeypatch: Any) -> StubOrchestrator:
    stub = StubOrchestrator()

    async def fake_get_orchestrator(*args: Any, **kwargs: Any) -> StubOrchestrator:
        _ = args, kwargs
        return stub

    monkeypatch.setattr(typer_cli, "get_orchestrator", fake_get_orchestrator)
    return stub


def test_cli_agent_list_outputs_agents(monkeypatch: Any) -> None:
    _patch_orchestrator(monkeypatch)
    result = runner.invoke(typer_cli.app, ["agent", "list"])

    assert result.exit_code == 0
    assert "agent-1" in result.stdout


def test_cli_agent_status_outputs_payload(monkeypatch: Any) -> None:
    _patch_orchestrator(monkeypatch)
    result = runner.invoke(typer_cli.app, ["agent", "status", "agent-1"])

    assert result.exit_code == 0
    assert "agent-1" in result.stdout


def test_cli_agent_accept_reject_commands(monkeypatch: Any) -> None:
    _patch_orchestrator(monkeypatch)

    accept_result = runner.invoke(typer_cli.app, ["agent", "accept", "agent-1"])
    reject_result = runner.invoke(typer_cli.app, ["agent", "reject", "agent-1"])

    assert accept_result.exit_code == 0
    assert "Queued accept" in accept_result.stdout
    assert reject_result.exit_code == 0
    assert "Queued reject" in reject_result.stdout


def test_cli_agent_spawn_queue_commands(monkeypatch: Any) -> None:
    _patch_orchestrator(monkeypatch)

    spawn_result = runner.invoke(typer_cli.app, ["agent", "spawn", "task"])
    queue_result = runner.invoke(typer_cli.app, ["agent", "queue", "task"])

    assert spawn_result.exit_code == 0
    assert "Spawned agent" in spawn_result.stdout
    assert queue_result.exit_code == 0
    assert "Queued agent" in queue_result.stdout


def test_cli_invalid_command() -> None:
    result = runner.invoke(typer_cli.app, ["agent", "unknown"])
    assert result.exit_code != 0
