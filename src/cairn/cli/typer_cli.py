"""Typer-based CLI interface for Cairn orchestrator.

This module provides the command-line interface using the Typer library,
offering commands for managing agent tasks, inspecting state, and controlling
workspaces.

The daemon commands (agent list/status/accept/reject/spawn/queue/run/undo/logs)
follow the thin-client contract from ``cairn.cli.cli``: mutations write signal
files for a running daemon, queries read the lifecycle mirror.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from fsdantic import Fsdantic, MergeStrategy
from cairn.cli.commands import (
    AcceptCommand,
    ListAgentsCommand,
    QueueCommand,
    RejectCommand,
    StatusCommand,
    parse_command_payload,
)
from cairn.core.exceptions import AgentNotFoundError, TimeoutError as CairnTimeoutError
from cairn.orchestrator.daemon import read_daemon_pid
from cairn.orchestrator.lifecycle import open_lifecycle_readonly
from cairn.orchestrator.signals import write_signal
from cairn.orchestrator.queue import TaskPriority
from cairn.runtime.agent import AgentState
from cairn.runtime.settings import PathsSettings

# Initialize Typer app and subcommands
app = typer.Typer(
    name="cairn-cli",
    help="Cairn CLI - Interact with Cairn workspaces, files, and agents",
    no_args_is_help=True,
)
workspace_app = typer.Typer(help="Workspace management commands")
files_app = typer.Typer(help="File operations in workspaces")
agent_app = typer.Typer(help="Agent management commands")
preview_app = typer.Typer(help="Preview and diff commands")

app.add_typer(workspace_app, name="workspace")
app.add_typer(files_app, name="files")
app.add_typer(agent_app, name="agent")
app.add_typer(preview_app, name="preview")

console = Console()


def get_paths_settings(
    project_root: Optional[Path] = None,
    cairn_home: Optional[Path] = None,
) -> PathsSettings:
    """Get path settings with optional overrides."""
    path_settings = PathsSettings()
    return PathsSettings(
        project_root=project_root or path_settings.project_root,
        cairn_home=cairn_home or path_settings.cairn_home,
    )


def _cairn_home(project_root: Optional[Path] = None, cairn_home: Optional[Path] = None) -> Path:
    return Path(get_paths_settings(project_root, cairn_home).cairn_home or Path.home() / ".cairn").expanduser()


# ============================================================================
# Workspace Commands
# ============================================================================


@workspace_app.command("create")
def workspace_create(
    name: Annotated[str, typer.Argument(help="Workspace name/ID")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Create a new workspace."""

    async def _create():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
        agentfs_dir.mkdir(parents=True, exist_ok=True)

        workspace_path = agentfs_dir / f"{name}.db"
        if workspace_path.exists():
            console.print(f"[red]Workspace '{name}' already exists at {workspace_path}[/red]")
            raise typer.Exit(1)

        workspace = await Fsdantic.open(path=str(workspace_path))
        await workspace.close()

        console.print(f"[green]✓[/green] Created workspace: [bold]{name}[/bold]")
        console.print(f"  Location: {workspace_path}")

    asyncio.run(_create())


@workspace_app.command("list")
def workspace_list(
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """List all workspaces in the project."""

    async def _list():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"

        if not agentfs_dir.exists():
            console.print("[yellow]No .agentfs directory found[/yellow]")
            return

        workspaces = sorted(agentfs_dir.glob("*.db"))

        if not workspaces:
            console.print("[yellow]No workspaces found[/yellow]")
            return

        table = Table(title="Cairn Workspaces")
        table.add_column("Name", style="cyan")
        table.add_column("Path", style="dim")
        table.add_column("Size", justify="right")

        for ws_path in workspaces:
            name = ws_path.stem
            size_mb = ws_path.stat().st_size / (1024 * 1024)
            table.add_row(name, str(ws_path), f"{size_mb:.2f} MB")

        console.print(table)

    asyncio.run(_list())


@workspace_app.command("info")
def workspace_info(
    name: Annotated[str, typer.Argument(help="Workspace name/ID")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Show information about a workspace."""

    async def _info():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
        workspace_path = agentfs_dir / f"{name}.db"

        if not workspace_path.exists():
            console.print(f"[red]Workspace '{name}' not found[/red]")
            raise typer.Exit(1)

        workspace = await Fsdantic.open(path=str(workspace_path), readonly=True)

        # Get file count and total size
        try:
            files = await workspace.files.search("**/*")
            file_count = len(files)

            total_size = 0
            for file_path in files:
                try:
                    stats = await workspace.files.stat(file_path)
                    if stats.is_file:
                        total_size += stats.size
                except Exception:
                    pass

            # Get KV count
            kv_entries = await workspace.kv.list(prefix="")
            kv_count = len(kv_entries)

            info_table = Table(title=f"Workspace Info: {name}", show_header=False)
            info_table.add_column("Property", style="cyan")
            info_table.add_column("Value", style="white")

            info_table.add_row("Name", name)
            info_table.add_row("Path", str(workspace_path))
            info_table.add_row("Database Size", f"{workspace_path.stat().st_size / (1024 * 1024):.2f} MB")
            info_table.add_row("Files", str(file_count))
            info_table.add_row("Total File Size", f"{total_size / 1024:.2f} KB")
            info_table.add_row("KV Entries", str(kv_count))

            console.print(info_table)

        finally:
            await workspace.close()

    asyncio.run(_info())


@workspace_app.command("delete")
def workspace_delete(
    name: Annotated[str, typer.Argument(help="Workspace name/ID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Delete a workspace."""

    async def _delete():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
        workspace_path = agentfs_dir / f"{name}.db"

        if not workspace_path.exists():
            console.print(f"[red]Workspace '{name}' not found[/red]")
            raise typer.Exit(1)

        if not force:
            confirm = typer.confirm(f"Delete workspace '{name}'?")
            if not confirm:
                console.print("[yellow]Cancelled[/yellow]")
                raise typer.Exit(0)

        workspace_path.unlink()
        console.print(f"[green]✓[/green] Deleted workspace: [bold]{name}[/bold]")

    asyncio.run(_delete())


# ============================================================================
# File Commands
# ============================================================================


@files_app.command("list")
def files_list(
    workspace: Annotated[str, typer.Argument(help="Workspace name/ID")],
    path: Annotated[str, typer.Option(help="Path to list")] = "/",
    recursive: Annotated[bool, typer.Option("--recursive", "-r", help="List recursively")] = False,
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """List files in a workspace."""

    async def _list():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
        workspace_path = agentfs_dir / f"{workspace}.db"

        if not workspace_path.exists():
            console.print(f"[red]Workspace '{workspace}' not found[/red]")
            raise typer.Exit(1)

        ws = await Fsdantic.open(path=str(workspace_path), readonly=True)

        try:
            if recursive:
                pattern = f"{path.rstrip('/')}/**/*" if path != "/" else "**/*"
                files = await ws.files.search(pattern)
                files = sorted(files)
            else:
                files = await ws.files.list_dir(path, output="full")

            if not files:
                console.print(f"[yellow]No files found in {path}[/yellow]")
                return

            table = Table(title=f"Files in {workspace}:{path}")
            table.add_column("Path", style="cyan")
            table.add_column("Type", style="dim")
            table.add_column("Size", justify="right")

            for file_path in files:
                try:
                    stats = await ws.files.stat(file_path)
                    file_type = "dir" if stats.is_directory else "file"
                    size = f"{stats.size:,}" if stats.is_file else "-"
                    table.add_row(file_path, file_type, size)
                except Exception as e:
                    table.add_row(file_path, "error", str(e))

            console.print(table)

        finally:
            await ws.close()

    asyncio.run(_list())


@files_app.command("read")
def files_read(
    workspace: Annotated[str, typer.Argument(help="Workspace name/ID")],
    path: Annotated[str, typer.Argument(help="File path to read")],
    binary: Annotated[bool, typer.Option("--binary", "-b", help="Read as binary")] = False,
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Read a file from a workspace."""

    async def _read():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
        workspace_path = agentfs_dir / f"{workspace}.db"

        if not workspace_path.exists():
            console.print(f"[red]Workspace '{workspace}' not found[/red]")
            raise typer.Exit(1)

        ws = await Fsdantic.open(path=str(workspace_path), readonly=True)

        try:
            mode = "binary" if binary else "text"
            content = await ws.files.read(path, mode=mode)

            if binary:
                console.print(f"[dim]Binary content ({len(content)} bytes)[/dim]")
                console.print(content[:200])
            else:
                text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
                console.print(Panel(text, title=f"{workspace}:{path}"))

        except Exception as e:
            console.print(f"[red]Error reading file: {e}[/red]")
            raise typer.Exit(1)
        finally:
            await ws.close()

    asyncio.run(_read())


@files_app.command("write")
def files_write(
    workspace: Annotated[str, typer.Argument(help="Workspace name/ID")],
    path: Annotated[str, typer.Argument(help="File path to write")],
    content: Annotated[str, typer.Argument(help="Content to write")],
    binary: Annotated[bool, typer.Option("--binary", "-b", help="Write as binary")] = False,
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Write a file to a workspace."""

    async def _write():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
        workspace_path = agentfs_dir / f"{workspace}.db"

        if not workspace_path.exists():
            console.print(f"[red]Workspace '{workspace}' not found[/red]")
            raise typer.Exit(1)

        ws = await Fsdantic.open(path=str(workspace_path))

        try:
            mode = "binary" if binary else "text"
            write_content = content.encode() if binary else content
            await ws.files.write(path, write_content, mode=mode)
            console.print(f"[green]✓[/green] Written to {workspace}:{path}")

        except Exception as e:
            console.print(f"[red]Error writing file: {e}[/red]")
            raise typer.Exit(1)
        finally:
            await ws.close()

    asyncio.run(_write())


@files_app.command("search")
def files_search(
    workspace: Annotated[str, typer.Argument(help="Workspace name/ID")],
    pattern: Annotated[str, typer.Argument(help="Glob pattern to search")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Search for files matching a pattern."""

    async def _search():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
        workspace_path = agentfs_dir / f"{workspace}.db"

        if not workspace_path.exists():
            console.print(f"[red]Workspace '{workspace}' not found[/red]")
            raise typer.Exit(1)

        ws = await Fsdantic.open(path=str(workspace_path), readonly=True)

        try:
            files = await ws.files.search(pattern)

            if not files:
                console.print(f"[yellow]No files found matching '{pattern}'[/yellow]")
                return

            console.print(f"[green]Found {len(files)} files matching '{pattern}':[/green]")
            for file_path in sorted(files):
                console.print(f"  {file_path}")

        finally:
            await ws.close()

    asyncio.run(_search())


@files_app.command("tree")
def files_tree(
    workspace: Annotated[str, typer.Argument(help="Workspace name/ID")],
    path: Annotated[str, typer.Option(help="Root path for tree")] = "/",
    max_depth: Annotated[Optional[int], typer.Option(help="Maximum depth to show")] = None,
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Show directory tree of a workspace."""

    async def _tree():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
        workspace_path = agentfs_dir / f"{workspace}.db"

        if not workspace_path.exists():
            console.print(f"[red]Workspace '{workspace}' not found[/red]")
            raise typer.Exit(1)

        ws = await Fsdantic.open(path=str(workspace_path), readonly=True)

        try:
            tree_data = await ws.files.tree(path, max_depth=max_depth)

            def build_tree(node, tree_obj):
                if node.get("type") == "directory":
                    branch = tree_obj.add(f"[bold cyan]{node['name']}[/bold cyan]/")
                    for child in node.get("children", []):
                        build_tree(child, branch)
                else:
                    tree_obj.add(f"[white]{node['name']}[/white]")

            tree = Tree(f"[bold]{workspace}:{path}[/bold]")
            for child in tree_data.get("children", []):
                build_tree(child, tree)

            console.print(tree)

        finally:
            await ws.close()

    asyncio.run(_tree())


# ============================================================================
# Agent Commands
# ============================================================================


@agent_app.command("list")
def agent_list(
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """List all agents (reads the lifecycle mirror read-only)."""

    async def _list():
        home = _cairn_home(project_root, cairn_home)
        try:
            async with open_lifecycle_readonly(home) as store:
                records = await store.list_all()
        except AgentNotFoundError:
            console.print("[yellow]No agents[/yellow]")
            return

        if not records:
            console.print("[yellow]No agents[/yellow]")
            return

        table = Table(title="Agents")
        table.add_column("Agent ID", style="cyan")
        table.add_column("State", style="yellow")
        table.add_column("Task", style="white")

        for record in sorted(records, key=lambda r: r.agent_id):
            table.add_row(record.agent_id, record.state.value, record.task)

        console.print(table)

    asyncio.run(_list())


@agent_app.command("status")
def agent_status(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Show detailed status of an agent."""

    async def _status():
        home = _cairn_home(project_root, cairn_home)
        try:
            async with open_lifecycle_readonly(home) as store:
                record = await store.load(agent_id)
        except AgentNotFoundError:
            console.print(f"[red]Unknown agent: {agent_id}[/red]")
            raise typer.Exit(1)
        if record is None:
            console.print(f"[red]Unknown agent: {agent_id}[/red]")
            raise typer.Exit(1)

        payload = {
            "state": record.state.value,
            "task": record.task,
            "error": record.error,
            "submission": record.submission,
            "files_written": record.files_written,
            "files_deleted": record.files_deleted,
            "claim_mismatch": record.claim_mismatch,
        }
        console.print(Panel(json.dumps(payload, indent=2), title=f"Agent Status: {agent_id}"))
        if record.claim_mismatch:
            claimed = sorted(record.submission["changed_files"]) if record.submission else []
            actual = sorted((record.run_written or []) + (record.run_deleted or []))
            console.print(f"agent claims : {', '.join(claimed) if claimed else '(nothing)'}")
            console.print(f"actually wrote: {', '.join(actual) if actual else '(nothing)'}")
            console.print("[red]! the agent's self-report does not match what it did[/red]")

    asyncio.run(_status())


def _mutation_required(home: Path) -> None:
    """Refuse a mutating command when no daemon is running."""
    if read_daemon_pid(home) is None:
        console.print(
            "[red]No Cairn daemon is running. Start one with `cairn up` (or run inline with `cairn run <task>`).[/red]"
        )
        raise typer.Exit(2)


async def _poll_state(home: Path, agent_id: str, states: set[AgentState], timeout: float):
    import time

    from cairn.orchestrator.lifecycle import LifecycleRecord

    deadline = time.monotonic() + timeout
    async with open_lifecycle_readonly(home) as store:
        while True:
            record: LifecycleRecord | None = await store.load(agent_id)
            if record is not None and record.state in states:
                return record
            if time.monotonic() >= deadline:
                raise CairnTimeoutError(
                    f"Agent {agent_id} did not settle within {timeout}s",
                    error_code="AGENT_WAIT_TIMEOUT",
                )
            await asyncio.sleep(0.1)


@agent_app.command("accept")
def agent_accept(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    force: Annotated[bool, typer.Option("--force", help="Accept even if stable changed since the agent started")] = False,
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds to wait for the accept to settle")] = 300.0,
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Accept an agent's changes (signal + poll)."""

    async def _accept():
        home = _cairn_home(project_root, cairn_home)
        _mutation_required(home)
        command = parse_command_payload("accept", {"agent_id": agent_id, "force": force})
        write_signal(home, command)

        record = await _poll_state(home, agent_id, {AgentState.ACCEPTED, AgentState.ERRORED}, timeout)
        if record.state is AgentState.ERRORED:
            console.print(f"[red]accept failed: {record.error}[/red]")
            raise typer.Exit(1)
        stats = record.accept_stats or {}
        console.print(f"[green]✓[/green] Accepted {agent_id}")
        console.print(f"  Merged {stats.get('files_merged', 0)} file(s) into stable")
        if stats.get("tombstones_applied"):
            console.print(f"  [yellow]Applied {stats['tombstones_applied']} deletion(s) to stable[/yellow]")

    asyncio.run(_accept())


@agent_app.command("reject")
def agent_reject(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds to wait for the reject to settle")] = 300.0,
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Reject an agent's changes (signal + poll)."""

    async def _reject():
        home = _cairn_home(project_root, cairn_home)
        _mutation_required(home)
        command = parse_command_payload("reject", {"agent_id": agent_id})
        write_signal(home, command)

        record = await _poll_state(home, agent_id, {AgentState.REJECTED, AgentState.ERRORED}, timeout)
        if record.state is AgentState.ERRORED:
            console.print(f"[red]reject failed: {record.error}[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] Rejected {agent_id}")

    asyncio.run(_reject())


@agent_app.command("spawn")
def agent_spawn(
    task: Annotated[str, typer.Argument(help="Task description for agent")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Spawn a high-priority agent task (signal)."""

    async def _spawn():
        home = _cairn_home(project_root, cairn_home)
        _mutation_required(home)
        command = parse_command_payload("spawn", {"task": task, "priority": int(TaskPriority.HIGH)})
        path = write_signal(home, command)
        console.print(f"[green]✓[/green] Spawned agent task ({path.name})")

    asyncio.run(_spawn())


@agent_app.command("queue")
def agent_queue(
    task: Annotated[str, typer.Argument(help="Task description for agent")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Queue a normal-priority agent task (signal)."""

    async def _queue():
        home = _cairn_home(project_root, cairn_home)
        _mutation_required(home)
        command = parse_command_payload("queue", {"task": task, "priority": int(TaskPriority.NORMAL)})
        path = write_signal(home, command)
        console.print(f"[green]✓[/green] Queued agent task ({path.name})")

    asyncio.run(_queue())


@agent_app.command("undo")
def agent_undo(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Undo an accepted agent's changes to stable (signal)."""

    async def _undo():
        home = _cairn_home(project_root, cairn_home)
        _mutation_required(home)
        command = parse_command_payload("undo", {"agent_id": agent_id})
        path = write_signal(home, command)
        console.print(f"[green]✓[/green] Submitted undo ({path.name})")

    asyncio.run(_undo())


@agent_app.command("logs")
def agent_logs(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Print an agent's sandbox run log."""

    async def _logs():
        home = _cairn_home(project_root, cairn_home)
        try:
            async with open_lifecycle_readonly(home) as store:
                record = await store.load(agent_id)
        except AgentNotFoundError:
            console.print(f"[red]Unknown agent: {agent_id}[/red]")
            raise typer.Exit(1)
        if record is None or not record.run_log:
            console.print(f"[red]No run log for {agent_id}[/red]")
            raise typer.Exit(1)
        console.print(record.run_log)

    asyncio.run(_logs())


@agent_app.command("run")
def agent_run(
    task: Annotated[str, typer.Argument(help="Task description for agent")],
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds to wait for the agent")] = 300.0,
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Run a single task inline to completion (no daemon)."""

    async def _run():
        from cairn.orchestrator.orchestrator import CairnOrchestrator
        from cairn.providers.providers import resolve_code_provider
        from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings

        home = _cairn_home(project_root, cairn_home)
        if read_daemon_pid(home) is not None:
            console.print("[red]A daemon is running; use `cairn queue` instead.[/red]")
            raise typer.Exit(2)

        path_settings = get_paths_settings(project_root, cairn_home)
        provider = resolve_code_provider("file", project_root=path_settings.project_root, base_path=None)
        orchestrator = CairnOrchestrator(
            project_root=path_settings.project_root or ".",
            cairn_home=home,
            config=OrchestratorSettings(),
            executor_settings=ExecutorSettings(),
            code_provider=provider,
        )
        await orchestrator.initialize()
        try:
            agent_id = await orchestrator.spawn_agent(task, TaskPriority.HIGH)
            record = await orchestrator.wait_for_agent(agent_id, timeout=timeout)
            console.print(json.dumps({"agent_id": agent_id, "state": record.state.value}, indent=2))
            if record.state is not AgentState.REVIEWING:
                raise typer.Exit(1)
        finally:
            await orchestrator.shutdown()

    asyncio.run(_run())


@agent_app.command("up")
def agent_up(
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Start the orchestrator daemon."""

    async def _up():
        import signal

        from cairn.orchestrator.daemon import daemon_pidfile
        from cairn.orchestrator.orchestrator import CairnOrchestrator
        from cairn.providers.providers import resolve_code_provider
        from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings

        home = _cairn_home(project_root, cairn_home)
        path_settings = get_paths_settings(project_root, cairn_home)
        provider = resolve_code_provider("file", project_root=path_settings.project_root, base_path=None)
        with daemon_pidfile(home):
            orchestrator = CairnOrchestrator(
                project_root=path_settings.project_root or ".",
                cairn_home=home,
                config=OrchestratorSettings(),
                executor_settings=ExecutorSettings(),
                code_provider=provider,
            )
            await orchestrator.initialize()

            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)

            runner = asyncio.create_task(orchestrator.run())
            try:
                await asyncio.wait(
                    [runner, asyncio.create_task(stop.wait())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                runner.cancel()
                with suppress(asyncio.CancelledError):
                    await runner
                await orchestrator.shutdown()

    asyncio.run(_up())


# ============================================================================
# Preview/Diff Commands
# ============================================================================


@preview_app.command("changes")
def preview_changes(
    agent_id: Annotated[str, typer.Argument(help="Agent ID to preview")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Preview changes made by an agent."""

    async def _preview():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"

        agent_db_path = agentfs_dir / f"{agent_id}.db"
        stable_db_path = agentfs_dir / "stable.db"

        if not agent_db_path.exists():
            console.print(f"[red]Agent workspace not found: {agent_id}[/red]")
            raise typer.Exit(1)
        if not stable_db_path.exists():
            console.print("[red]Stable workspace not found (stable.db missing)[/red]")
            raise typer.Exit(1)

        agent_ws = await Fsdantic.open(path=str(agent_db_path), readonly=True)
        stable_ws = await Fsdantic.open(path=str(stable_db_path), readonly=True)

        try:
            changes = await agent_ws.materialize.diff(stable_ws)

            if not changes:
                console.print(f"[yellow]No changes found for agent {agent_id}[/yellow]")
                return

            table = Table(title=f"Changes by Agent: {agent_id}")
            table.add_column("Change Type", style="cyan")
            table.add_column("Path", style="white")
            table.add_column("Old Size", justify="right")
            table.add_column("New Size", justify="right")

            for change in changes:
                old_size = f"{change.old_size:,}" if change.old_size is not None else "-"
                new_size = f"{change.new_size:,}" if change.new_size is not None else "-"
                table.add_row(change.change_type, change.path, old_size, new_size)

            console.print(table)
            console.print(f"\n[green]Total changes: {len(changes)}[/green]")

        finally:
            await agent_ws.close()
            await stable_ws.close()

    asyncio.run(_preview())


@preview_app.command("file")
def preview_file(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    file_path: Annotated[str, typer.Argument(help="File path to preview")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Preview a specific file from an agent's workspace."""

    async def _preview():
        path_settings = get_paths_settings(project_root, cairn_home)
        agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"

        agent_db_path = agentfs_dir / f"{agent_id}.db"

        if not agent_db_path.exists():
            console.print(f"[red]Agent workspace not found: {agent_id}[/red]")
            raise typer.Exit(1)

        agent_ws = await Fsdantic.open(path=str(agent_db_path), readonly=True)

        try:
            content = await agent_ws.files.read(file_path, mode="text")
            console.print(Panel(content, title=f"Agent {agent_id}: {file_path}"))

        except Exception as e:
            console.print(f"[red]Error reading file: {e}[/red]")
            raise typer.Exit(1)
        finally:
            await agent_ws.close()

    asyncio.run(_preview())


def main():
    """Entry point for the Typer CLI."""
    app()


if __name__ == "__main__":
    main()
