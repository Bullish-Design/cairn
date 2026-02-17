"""Typer-based CLI interface for Cairn orchestrator.

This module provides the command-line interface using the Typer library,
offering commands for managing agent tasks, inspecting state, and controlling
the orchestrator lifecycle.

The CLI communicates with the orchestrator through the command pattern defined
in commands.py, providing a user-friendly interface for all orchestrator operations.
"""

from __future__ import annotations

import asyncio
import json
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
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import CodeProvider, resolve_code_provider
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.orchestrator.queue import TaskPriority

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


def resolve_provider(
    provider: str,
    project_root: Optional[Path],
    provider_base_path: Optional[Path],
) -> CodeProvider:
    return resolve_code_provider(
        provider,
        project_root=project_root,
        base_path=provider_base_path,
    )


async def get_orchestrator(
    project_root: Optional[Path] = None,
    cairn_home: Optional[Path] = None,
    provider: str = "file",
    provider_base_path: Optional[Path] = None,
) -> CairnOrchestrator:
    """Create and initialize an orchestrator instance."""
    path_settings = get_paths_settings(project_root, cairn_home)
    provider_instance = resolve_provider(provider, path_settings.project_root, provider_base_path)
    orchestrator = CairnOrchestrator(
        project_root=path_settings.project_root or ".",
        cairn_home=path_settings.cairn_home,
        config=OrchestratorSettings(),
        executor_settings=ExecutorSettings(),
        code_provider=provider_instance,
    )
    await orchestrator.initialize()
    return orchestrator


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

        workspace = await Fsdantic.open(path=str(workspace_path))

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

        ws = await Fsdantic.open(path=str(workspace_path))

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

        ws = await Fsdantic.open(path=str(workspace_path))

        try:
            mode = "binary" if binary else "text"
            content = await ws.files.read(path, mode=mode)

            if binary:
                console.print(f"[dim]Binary content ({len(content)} bytes)[/dim]")
                console.print(content[:200])
            else:
                console.print(Panel(content, title=f"{workspace}:{path}"))

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

        ws = await Fsdantic.open(path=str(workspace_path))

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

        ws = await Fsdantic.open(path=str(workspace_path))

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
    """List all active agents."""

    async def _list():
        orchestrator = await get_orchestrator(project_root, cairn_home)

        try:
            command = parse_command_payload("list_agents", {})
            result = await orchestrator.submit_command(command)
            agents = result.payload.get("agents", {})

            if not agents:
                console.print("[yellow]No active agents[/yellow]")
                return

            table = Table(title="Active Agents")
            table.add_column("Agent ID", style="cyan")
            table.add_column("State", style="yellow")
            table.add_column("Task", style="white")
            table.add_column("Priority", justify="right")

            for agent_id, agent_data in sorted(agents.items()):
                table.add_row(
                    agent_id,
                    agent_data.get("state", "unknown"),
                    agent_data.get("task", ""),
                    str(agent_data.get("priority", "")),
                )

            console.print(table)

        finally:
            if orchestrator.stable:
                await orchestrator.stable.close()
            if orchestrator.bin:
                await orchestrator.bin.close()

    asyncio.run(_list())


@agent_app.command("status")
def agent_status(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Show detailed status of an agent."""

    async def _status():
        orchestrator = await get_orchestrator(project_root, cairn_home)

        try:
            command = parse_command_payload("status", {"agent_id": agent_id})
            result = await orchestrator.submit_command(command)

            console.print(
                Panel(
                    json.dumps(result.payload, indent=2),
                    title=f"Agent Status: {agent_id}",
                )
            )

        except ValueError:
            console.print(f"[red]Unknown agent: {agent_id}[/red]")
            raise typer.Exit(1)
        finally:
            if orchestrator.stable:
                await orchestrator.stable.close()
            if orchestrator.bin:
                await orchestrator.bin.close()

    asyncio.run(_status())


@agent_app.command("accept")
def agent_accept(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Accept an agent's changes."""

    async def _accept():
        orchestrator = await get_orchestrator(project_root, cairn_home)

        try:
            command = parse_command_payload("accept", {"agent_id": agent_id})
            await orchestrator.submit_command(command)
            console.print(f"[green]✓[/green] Queued accept for {agent_id}")

        finally:
            if orchestrator.stable:
                await orchestrator.stable.close()
            if orchestrator.bin:
                await orchestrator.bin.close()

    asyncio.run(_accept())


@agent_app.command("reject")
def agent_reject(
    agent_id: Annotated[str, typer.Argument(help="Agent ID")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
):
    """Reject an agent's changes."""

    async def _reject():
        orchestrator = await get_orchestrator(project_root, cairn_home)

        try:
            command = parse_command_payload("reject", {"agent_id": agent_id})
            await orchestrator.submit_command(command)
            console.print(f"[green]✓[/green] Queued reject for {agent_id}")

        finally:
            if orchestrator.stable:
                await orchestrator.stable.close()
            if orchestrator.bin:
                await orchestrator.bin.close()

    asyncio.run(_reject())


@agent_app.command("spawn")
def agent_spawn(
    task: Annotated[str, typer.Argument(help="Task description for agent")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
    provider: Annotated[str, typer.Option(help="Code provider name (file, inline, or plugin)")] = "file",
    provider_base_path: Annotated[Optional[Path], typer.Option(help="Base path for file provider")] = None,
):
    """Spawn a high-priority agent task."""

    async def _spawn():
        orchestrator = await get_orchestrator(
            project_root,
            cairn_home,
            provider=provider,
            provider_base_path=provider_base_path,
        )

        try:
            command = parse_command_payload("spawn", {"task": task, "priority": int(TaskPriority.HIGH)})
            await orchestrator.submit_command(command)
            console.print("[green]✓[/green] Spawned agent task")

        finally:
            if orchestrator.stable:
                await orchestrator.stable.close()
            if orchestrator.bin:
                await orchestrator.bin.close()

    asyncio.run(_spawn())


@agent_app.command("queue")
def agent_queue(
    task: Annotated[str, typer.Argument(help="Task description for agent")],
    project_root: Annotated[Optional[Path], typer.Option(help="Project root directory")] = None,
    cairn_home: Annotated[Optional[Path], typer.Option(help="Cairn home directory")] = None,
    provider: Annotated[str, typer.Option(help="Code provider name (file, inline, or plugin)")] = "file",
    provider_base_path: Annotated[Optional[Path], typer.Option(help="Base path for file provider")] = None,
):
    """Queue a normal-priority agent task."""

    async def _queue():
        orchestrator = await get_orchestrator(
            project_root,
            cairn_home,
            provider=provider,
            provider_base_path=provider_base_path,
        )

        try:
            command = parse_command_payload("queue", {"task": task, "priority": int(TaskPriority.NORMAL)})
            await orchestrator.submit_command(command)
            console.print("[green]✓[/green] Queued agent task")

        finally:
            if orchestrator.stable:
                await orchestrator.stable.close()
            if orchestrator.bin:
                await orchestrator.bin.close()

    asyncio.run(_queue())


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

        agent_db_path = agentfs_dir / f"agent-{agent_id}.db"
        stable_db_path = agentfs_dir / "stable.db"

        if not agent_db_path.exists():
            console.print(f"[red]Agent workspace not found: {agent_id}[/red]")
            raise typer.Exit(1)

        agent_ws = await Fsdantic.open(path=str(agent_db_path))
        stable_ws = await Fsdantic.open(path=str(stable_db_path))

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

        agent_db_path = agentfs_dir / f"agent-{agent_id}.db"

        if not agent_db_path.exists():
            console.print(f"[red]Agent workspace not found: {agent_id}[/red]")
            raise typer.Exit(1)

        agent_ws = await Fsdantic.open(path=str(agent_db_path))

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
