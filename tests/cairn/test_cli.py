import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from cairn.cli import cli
from cairn.orchestrator.lifecycle import LifecycleRecord
from cairn.runtime.agent import AgentState


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


def test_cli_agent_list_outputs_agents(tmp_path: Path, capsys: Any) -> None:
    home = _seed_mirror(tmp_path)
    rc = cli.main(["list-agents", "--cairn-home", str(home)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "agent-1" in captured.out


def test_cli_agent_status_outputs_payload(tmp_path: Path, capsys: Any) -> None:
    home = _seed_mirror(tmp_path)
    rc = cli.main(["status", "agent-1", "--cairn-home", str(home)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "agent-1" in captured.out


def test_cli_agent_accept_reject_commands(tmp_path: Path, capsys: Any) -> None:
    """Without a running daemon, mutating commands refuse with exit 2."""
    home = _seed_mirror(tmp_path)

    accept_rc = cli.main(["accept", "agent-1", "--cairn-home", str(home)])
    accept_err = capsys.readouterr().err
    reject_rc = cli.main(["reject", "agent-1", "--cairn-home", str(home)])
    reject_err = capsys.readouterr().err

    assert accept_rc == 2
    assert "No Cairn daemon" in accept_err
    assert reject_rc == 2
    assert "No Cairn daemon" in reject_err


def test_cli_agent_spawn_queue_commands(tmp_path: Path, capsys: Any) -> None:
    """Without a running daemon, mutating commands refuse with exit 2."""
    home = _seed_mirror(tmp_path)

    spawn_rc = cli.main(["spawn", "task", "--cairn-home", str(home)])
    spawn_err = capsys.readouterr().err
    queue_rc = cli.main(["queue", "task", "--cairn-home", str(home)])
    queue_err = capsys.readouterr().err

    assert spawn_rc == 2
    assert "No Cairn daemon" in spawn_err
    assert queue_rc == 2
    assert "No Cairn daemon" in queue_err


def test_cli_agent_logs_shows_run_log(tmp_path: Path, capsys: Any) -> None:
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
    rc = cli.main(["logs", "agent-logs", "--cairn-home", str(home)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "RuntimeError: boom" in captured.out


def test_cli_invalid_command() -> None:
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["unknown-command"])


def test_common_flags_bind_before_and_after_subcommand() -> None:
    """SPEC: the --project-root/--cairn-home/provider flags work on every
    command.  Both positions must bind — a flag given *before* the subcommand
    must not be silently discarded when the subparser's own defaults are
    written into the shared namespace."""
    parser = cli.build_parser()

    before = parser.parse_args(["--cairn-home", "/tmp/BEFORE", "--project-root", "/tmp/PBEFORE", "list-agents"])
    assert before.project_root == "/tmp/PBEFORE"
    assert before.cairn_home == "/tmp/BEFORE"

    after = parser.parse_args(["list-agents", "--cairn-home", "/tmp/AFTER", "--project-root", "/tmp/PAFTER"])
    assert after.project_root == "/tmp/PAFTER"
    assert after.cairn_home == "/tmp/AFTER"

    # Both positions: the later (subcommand-side) value wins, as with any
    # repeated option.
    both = parser.parse_args(
        ["--cairn-home", "/tmp/BEFORE", "--project-root", "/tmp/PBEFORE", "list-agents"]
        + ["--cairn-home", "/tmp/AFTER", "--project-root", "/tmp/PAFTER"]
    )
    assert both.project_root == "/tmp/PAFTER"
    assert both.cairn_home == "/tmp/AFTER"

    none = parser.parse_args(["list-agents"])
    assert none.project_root is None
    assert none.cairn_home is None

    # Provider flags bind in both positions too.
    provider_before = parser.parse_args(["--provider", "inline", "list-agents"])
    assert provider_before.provider == "inline"
    provider_after = parser.parse_args(["list-agents", "--provider", "inline"])
    assert provider_after.provider == "inline"


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


def test_cli_inspection_commands_open_readonly(tmp_path: Any, monkeypatch: Any, capsys: Any) -> None:
    """Inspection commands (info/list/read/search/tree) must open workspaces
    read-only; mutating commands (create/write) must not."""
    import fsdantic as fsdantic_mod
    from fsdantic import Fsdantic

    project_root = tmp_path / "project"
    _seed_cli_workspaces(project_root)

    calls: list[dict[str, Any]] = []
    real_open = Fsdantic.open

    @classmethod
    async def spy_open(cls, **kwargs: Any):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return await real_open(**kwargs)

    monkeypatch.setattr(fsdantic_mod.Fsdantic, "open", spy_open)

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
        rc = cli.main(cmd + args)
        assert rc == 0, f"{cmd} failed: {capsys.readouterr().err}"
        assert calls, f"{cmd} opened no workspace"
        assert all(call.get("readonly") is True for call in calls), f"{cmd} not readonly: {calls}"

    # Mutating commands must NOT open read-only.
    for cmd in [
        ["workspace", "create", "brand-new"],
        ["files", "write", "my", "new.txt", "hello"],
    ]:
        calls.clear()
        rc = cli.main(cmd + args)
        assert rc == 0, f"{cmd} failed: {capsys.readouterr().err}"
        assert calls
        assert all(call.get("readonly") is not True for call in calls), f"{cmd} should be read-write: {calls}"


def test_cli_preview_changes_requires_workspace(tmp_path: Any) -> None:
    """preview changes must error cleanly when the agent's disposable
    workspace is missing instead of crashing (no agent db / no stable.db)."""
    project_root = tmp_path / "project"
    (project_root / ".agentfs").mkdir(parents=True, exist_ok=True)

    import contextlib
    import io

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        rc = cli.main(["preview", "changes", "agent-1", "--project-root", str(project_root)])

    assert rc == 1
    assert "workspace not found for agent" in stderr.getvalue()


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


def test_managed_workspace_mutation_is_refused(tmp_path: Any) -> None:
    """Review §2.8: direct mutation of Cairn-managed workspaces (stable, bin,
    agent-*/bin-*) is refused — there is no `files write stable` bypass."""
    project_root = tmp_path / "project"
    (project_root / ".agentfs").mkdir(parents=True)
    _seed_cli_workspaces(project_root)  # creates stable.db, agent-1.db, my.db

    for cmd in [
        ["files", "write", "stable", "x.txt", "evil"],
        ["files", "write", "bin", "x.txt", "evil"],
        ["workspace", "delete", "stable", "--force"],
        ["workspace", "delete", "agent-1", "--force"],
    ]:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(cmd + ["--project-root", str(project_root)])
        assert "managed by Cairn" in str(excinfo.value), cmd

    # The managed databases are untouched.
    assert (project_root / ".agentfs" / "stable.db").exists()
    assert (project_root / ".agentfs" / "agent-1.db").exists()


def test_workspace_name_traversal_is_rejected(tmp_path: Any) -> None:
    """Review §2.8: workspace names are validated — no traversal, no path
    separators, no dot-dot escapes out of .agentfs."""
    import pytest as _pytest

    project_root = tmp_path / "project"
    (project_root / ".agentfs").mkdir(parents=True)

    for bad in ("../escape", "a/b", "..", ".", "a\\b", "name with spaces"):
        with _pytest.raises(SystemExit):
            cli.main(["workspace", "create", bad, "--project-root", str(project_root)])

    # Nothing escaped .agentfs.
    assert not (tmp_path / "escape.db").exists()
    assert list((project_root / ".agentfs").glob("*.db")) == []
