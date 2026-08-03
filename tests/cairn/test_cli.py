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
    assert "Accepted agent-1" in accept_result.stdout
    assert "Merged 0 file(s) into stable" in accept_result.stdout
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


def _seed_cli_workspaces(project_root: Any) -> None:
    """Create stable.db + agent-1.db + my.db with content under project_root/.agentfs."""
    import asyncio

    from fsdantic import Fsdantic

    agentfs_dir = project_root / ".agentfs"
    agentfs_dir.mkdir(parents=True, exist_ok=True)

    async def _seed() -> None:
        stable = await Fsdantic.open(path=str(agentfs_dir / "stable.db"))
        await stable.files.write("keep.txt", "keep")
        await stable.close()

        agent = await Fsdantic.open(path=str(agentfs_dir / "agent-1.db"))
        await agent.files.write("overlay.txt", "overlay")
        await agent.files.write("keep.txt", "agent copy")
        await agent.close()

        ws = await Fsdantic.open(path=str(agentfs_dir / "my.db"))
        await ws.files.write("file.txt", "content")
        await ws.close()

    asyncio.run(_seed())


def test_cli_inspection_commands_open_readonly(tmp_path: Any, monkeypatch: Any) -> None:
    """Inspection commands (info/list/read/search/tree/preview) must open
    workspaces read-only; mutating commands (create/write) must not."""
    from fsdantic import Fsdantic

    project_root = tmp_path / "project"
    _seed_cli_workspaces(project_root)

    calls: list[dict[str, Any]] = []
    real_open = Fsdantic.open

    @classmethod
    async def spy_open(cls, **kwargs: Any):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return await real_open(**kwargs)

    monkeypatch.setattr(typer_cli.Fsdantic, "open", spy_open)

    args = ["--project-root", str(project_root)]
    inspection_cmds = [
        ["workspace", "info", "my"],
        ["files", "list", "my"],
        ["files", "read", "my", "file.txt"],
        ["files", "search", "my", "**/*"],
        ["files", "tree", "my"],
        ["preview", "changes", "agent-1"],
        ["preview", "file", "agent-1", "overlay.txt"],
    ]
    for cmd in inspection_cmds:
        calls.clear()
        result = runner.invoke(typer_cli.app, cmd + args)
        assert result.exit_code == 0, f"{cmd} failed: {result.stdout}"
        assert calls, f"{cmd} opened no workspace"
        assert all(call.get("readonly") is True for call in calls), f"{cmd} not readonly: {calls}"

    # Mutating commands must NOT open read-only.
    for cmd in [
        ["workspace", "create", "brand-new"],
        ["files", "write", "my", "new.txt", "hello"],
    ]:
        calls.clear()
        result = runner.invoke(typer_cli.app, cmd + args)
        assert result.exit_code == 0, f"{cmd} failed: {result.stdout}"
        assert calls
        assert all(call.get("readonly") is not True for call in calls), f"{cmd} should be read-write: {calls}"


def test_cli_preview_changes_requires_stable(tmp_path: Any) -> None:
    """preview changes must error cleanly when stable.db is missing instead
    of silently creating it (read-only open of a nonexistent DB)."""
    project_root = tmp_path / "project"
    agentfs_dir = project_root / ".agentfs"
    agentfs_dir.mkdir(parents=True, exist_ok=True)

    import asyncio

    from fsdantic import Fsdantic

    async def _seed() -> None:
        agent = await Fsdantic.open(path=str(agentfs_dir / "agent-1.db"))
        await agent.files.write("overlay.txt", "overlay")
        await agent.close()

    asyncio.run(_seed())

    result = runner.invoke(
        typer_cli.app,
        ["preview", "changes", "agent-1", "--project-root", str(project_root)],
    )

    assert result.exit_code == 1
    assert "stable.db missing" in result.stdout
