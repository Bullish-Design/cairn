import asyncio
import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from cairn.cli import cli, typer_cli
from cairn.orchestrator.lifecycle import LifecycleRecord
from cairn.runtime.agent import AgentState

runner = CliRunner()


def _write_lifecycle_mirror(home: Path, records: list[LifecycleRecord]) -> None:
    """Write the lifecycle mirror the thin CLI reads."""
    from cairn.orchestrator.lifecycle import lifecycle_mirror_path

    path = lifecycle_mirror_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {record.agent_id: record.model_dump(mode="json") for record in records}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_mirror(tmp_path: Path) -> Path:
    """Seed a lifecycle mirror with one agent record; returns cairn_home."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".agentfs").mkdir(parents=True)
    _write_lifecycle_mirror(
        home,
        [
            LifecycleRecord(
                agent_id="agent-1",
                task="task",
                priority=2,
                state=AgentState.QUEUED,
                state_changed_at=1.0,
                created_at=1.0,
                db_path=str(project / ".agentfs" / "agent-1.db"),
            )
        ],
    )
    return home


def test_cli_agent_list_outputs_agents(tmp_path: Path) -> None:
    home = _seed_mirror(tmp_path)
    result = runner.invoke(typer_cli.app, ["agent", "list", "--cairn-home", str(home)])

    assert result.exit_code == 0
    assert "agent-1" in result.stdout


def test_cli_agent_status_outputs_payload(tmp_path: Path) -> None:
    home = _seed_mirror(tmp_path)
    result = runner.invoke(typer_cli.app, ["agent", "status", "agent-1", "--cairn-home", str(home)])

    assert result.exit_code == 0
    assert "agent-1" in result.stdout


def test_cli_agent_accept_reject_commands(tmp_path: Path) -> None:
    """Without a running daemon, mutating commands refuse with exit 2."""
    home = _seed_mirror(tmp_path)

    accept_result = runner.invoke(typer_cli.app, ["agent", "accept", "agent-1", "--cairn-home", str(home)])
    reject_result = runner.invoke(typer_cli.app, ["agent", "reject", "agent-1", "--cairn-home", str(home)])

    assert accept_result.exit_code == 2
    assert "No Cairn daemon" in accept_result.stdout
    assert reject_result.exit_code == 2
    assert "No Cairn daemon" in reject_result.stdout


def test_cli_agent_spawn_queue_commands(tmp_path: Path) -> None:
    """Without a running daemon, mutating commands refuse with exit 2."""
    home = _seed_mirror(tmp_path)

    spawn_result = runner.invoke(typer_cli.app, ["agent", "spawn", "task", "--cairn-home", str(home)])
    queue_result = runner.invoke(typer_cli.app, ["agent", "queue", "task", "--cairn-home", str(home)])

    assert spawn_result.exit_code == 2
    assert "No Cairn daemon" in spawn_result.stdout
    assert queue_result.exit_code == 2
    assert "No Cairn daemon" in queue_result.stdout


def test_cli_agent_logs_shows_run_log(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".agentfs").mkdir(parents=True)
    _write_lifecycle_mirror(
        home,
        [
            LifecycleRecord(
                agent_id="agent-logs",
                task="t",
                priority=3,
                state=AgentState.ERRORED,
                state_changed_at=1.0,
                created_at=1.0,
                db_path=str(project / ".agentfs" / "agent-logs.db"),
                run_log="RuntimeError: boom\n",
            )
        ],
    )
    result = runner.invoke(typer_cli.app, ["agent", "logs", "agent-logs", "--cairn-home", str(home)])
    assert result.exit_code == 0
    assert "RuntimeError: boom" in result.stdout


def test_cli_invalid_command() -> None:
    result = runner.invoke(typer_cli.app, ["agent", "unknown"])
    assert result.exit_code != 0


def _seed_cli_workspaces(project_root: Any) -> None:
    """Create stable.db + agent-1.db + my.db with content under project_root/.agentfs."""

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


def test_cli_preview_changes_requires_workspace(tmp_path: Any) -> None:
    """preview changes must error cleanly when the agent's disposable
    workspace is missing instead of crashing (no agent db / no stable.db)."""
    project_root = tmp_path / "project"
    (project_root / ".agentfs").mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        typer_cli.app,
        ["preview", "changes", "agent-1", "--project-root", str(project_root)],
    )

    assert result.exit_code == 1
    assert "Workspace not found for agent" in result.stdout


def test_status_does_not_start_a_worker(tmp_path: Path, monkeypatch: Any) -> None:
    """P1.4: `cairn status` reads the lifecycle mirror read-only; it must not
    construct an orchestrator (no orchestrator.json, no agent-*.db, no
    recovery side effects)."""
    project = tmp_path / "project"
    agentfs = project / ".agentfs"
    agentfs.mkdir(parents=True)
    home = tmp_path / "home"

    monkeypatch.setenv("CAIRN_PATHS_PROJECT_ROOT", str(project))
    monkeypatch.setenv("CAIRN_PATHS_CAIRN_HOME", str(home))

    # Seed the lifecycle mirror as a daemon would leave it.
    record = LifecycleRecord(
        agent_id="agent-status-only",
        task="some task",
        priority=3,
        state=AgentState.REVIEWING,
        state_changed_at=1.0,
        created_at=1.0,
        db_path=str(agentfs / "bin-agent-status-only.db"),
    )
    _write_lifecycle_mirror(home, [record])

    rc = cli.main(["status", "agent-status-only"])
    assert rc == 0

    # No worker side effects: no agent-*.db, no orchestrator.json, and the
    # lifecycle record's state was not touched.
    assert list(agentfs.glob("agent-*.db")) == []
    assert not (home / "state" / "orchestrator.json").exists()
    assert record.state is AgentState.REVIEWING


def test_status_unknown_agent_exits_1(tmp_path: Path, monkeypatch: Any) -> None:
    """P1.6: `cairn status agent-nope` prints a friendly message and exits 1,
    not a traceback."""
    project = tmp_path / "project"
    agentfs = project / ".agentfs"
    agentfs.mkdir(parents=True)
    home = tmp_path / "home"

    monkeypatch.setenv("CAIRN_PATHS_PROJECT_ROOT", str(project))
    monkeypatch.setenv("CAIRN_PATHS_CAIRN_HOME", str(home))

    _write_lifecycle_mirror(home, [])

    import io

    stderr = io.StringIO()
    import contextlib

    with contextlib.redirect_stderr(stderr):
        rc = cli.main(["status", "agent-nope"])
    assert rc == 1
    assert "Unknown agent: agent-nope" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_logs_shows_run_log(tmp_path: Path, monkeypatch: Any) -> None:
    """P4.3: `cairn logs <id>` prints the sandbox run log from the mirror."""
    import contextlib
    import io

    project = tmp_path / "project"
    agentfs = project / ".agentfs"
    agentfs.mkdir(parents=True)
    home = tmp_path / "home"

    monkeypatch.setenv("CAIRN_PATHS_PROJECT_ROOT", str(project))
    monkeypatch.setenv("CAIRN_PATHS_CAIRN_HOME", str(home))

    record = LifecycleRecord(
        agent_id="agent-logs",
        task="t",
        priority=3,
        state=AgentState.ERRORED,
        state_changed_at=1.0,
        created_at=1.0,
        db_path=str(agentfs / "bin-agent-logs.db"),
        run_log="traceback here\n  File bad.py, line 1\nRuntimeError: boom\n",
    )
    _write_lifecycle_mirror(home, [record])

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        rc = cli.main(["logs", "agent-logs"])
    assert rc == 0
    assert "RuntimeError: boom" in stdout.getvalue()


def test_logs_unknown_agent(tmp_path: Path, monkeypatch: Any) -> None:
    project = tmp_path / "project"
    agentfs = project / ".agentfs"
    agentfs.mkdir(parents=True)
    home = tmp_path / "home"
    monkeypatch.setenv("CAIRN_PATHS_PROJECT_ROOT", str(project))
    monkeypatch.setenv("CAIRN_PATHS_CAIRN_HOME", str(home))

    import contextlib
    import io

    _write_lifecycle_mirror(home, [])

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        rc = cli.main(["logs", "agent-nope"])
    assert rc == 1
    assert "Unknown agent: agent-nope" in stderr.getvalue()
