"""Command-line interface for the Cairn orchestrator service.

The daemon owns the databases.  The CLI writes signals for mutating commands
and reads the lifecycle store read-only for queries, so no subcommand ever
constructs a full orchestrator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from contextlib import suppress
from pathlib import Path

from cairn.cli.commands import (
    CairnCommand,
    CommandType,
    parse_command_payload,
)
from cairn.core.exceptions import AgentNotFoundError
from cairn.orchestrator.daemon import (
    read_daemon_pid,
    remove_daemon_pid,
    write_daemon_pid,
)
from cairn.orchestrator.lifecycle import open_lifecycle_readonly
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority
from cairn.orchestrator.transport import daemon_running, send_request
from cairn.providers.providers import CodeProvider, resolve_code_provider
from cairn.runtime.agent import AgentState
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings, PathsSettings


def _resolve_settings(args: argparse.Namespace) -> tuple[PathsSettings, OrchestratorSettings, ExecutorSettings]:
    path_settings = PathsSettings()
    orchestrator_settings = OrchestratorSettings()
    executor_settings = ExecutorSettings()

    return (
        PathsSettings(
            project_root=Path(args.project_root) if args.project_root is not None else path_settings.project_root,
            cairn_home=Path(args.cairn_home) if args.cairn_home is not None else path_settings.cairn_home,
        ),
        OrchestratorSettings(
            max_concurrent_agents=(
                args.max_concurrent_agents
                if args.max_concurrent_agents is not None
                else orchestrator_settings.max_concurrent_agents
            ),
        ),
        ExecutorSettings(
            max_execution_time=(
                args.max_execution_time if args.max_execution_time is not None else executor_settings.max_execution_time
            ),
            max_memory_bytes=(
                args.max_memory_bytes if args.max_memory_bytes is not None else executor_settings.max_memory_bytes
            ),
            max_recursion_depth=(
                args.max_recursion_depth
                if args.max_recursion_depth is not None
                else executor_settings.max_recursion_depth
            ),
        ),
    )


def _resolve_cairn_home(args: argparse.Namespace) -> Path:
    path_settings, *_ = _resolve_settings(args)
    return Path(path_settings.cairn_home or Path.home() / ".cairn").expanduser()


def _resolve_provider(args: argparse.Namespace) -> CodeProvider:
    path_settings, *_ = _resolve_settings(args)
    return resolve_code_provider(
        args.provider,
        project_root=path_settings.project_root,
        base_path=Path(args.provider_base_path) if args.provider_base_path else None,
    )


async def _run_up(args: argparse.Namespace) -> int:
    path_settings, orchestrator_settings, executor_settings = _resolve_settings(args)
    cairn_home = _resolve_cairn_home(args)
    provider = _resolve_provider(args)
    orchestrator = CairnOrchestrator(
        project_root=path_settings.project_root or ".",
        cairn_home=cairn_home,
        config=orchestrator_settings,
        executor_settings=executor_settings,
        code_provider=provider,
    )
    try:
        await orchestrator.initialize()  # binds the control socket (ownership)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    write_daemon_pid(cairn_home)

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
        remove_daemon_pid(cairn_home)
    return 0


async def _dispatch_mutation(args: argparse.Namespace, command: CairnCommand) -> int:
    """Send a mutating command over the daemon transport and await its
    result synchronously (review §3.1 — no signal files, no stale polls)."""
    cairn_home = _resolve_cairn_home(args)

    if not daemon_running(cairn_home):
        print(
            "No Cairn daemon is running.\n"
            "  Start one with:  cairn up\n"
            "  Or run this task inline with:  cairn run <task>",
            file=sys.stderr,
        )
        return 2

    try:
        response = await send_request(cairn_home, command, timeout=getattr(args, "timeout", 60.0))
    except (ConnectionError, TimeoutError, OSError) as exc:
        print(f"{command.type.value} failed: {exc}", file=sys.stderr)
        return 1

    if not response.ok:
        print(f"{command.type.value} failed: {response.error}", file=sys.stderr)
        return 1

    result = response.result or {}
    if command.type in (CommandType.ACCEPT, CommandType.UNDO):
        print(
            f"{command.type.value} {args.agent_id}: "
            f"{result.get('files_written', result.get('restored', 0))} file(s) written, "
            f"{result.get('files_deleted', result.get('deleted', 0))} deletion(s) applied"
        )
    else:
        print(f"submitted {command.type.value}")
    return 0


async def _run_spawn(args: argparse.Namespace) -> int:
    command = parse_command_payload("spawn", {"task": args.task, "priority": int(TaskPriority.HIGH)})
    return await _dispatch_mutation(args, command)


async def _run_queue(args: argparse.Namespace) -> int:
    command = parse_command_payload("queue", {"task": args.task, "priority": int(TaskPriority.NORMAL)})
    return await _dispatch_mutation(args, command)


async def _run_list_agents(args: argparse.Namespace) -> int:
    cairn_home = _resolve_cairn_home(args)
    try:
        async with open_lifecycle_readonly(cairn_home) as store:
            records = await store.list_all()
    except AgentNotFoundError:
        print("No agents")
        return 0
    if not records:
        print("No agents")
        return 0
    for record in sorted(records, key=lambda r: r.agent_id):
        print(f"{record.agent_id}\t{record.state.value}\t{record.task}")
    return 0


async def _run_status(args: argparse.Namespace) -> int:
    cairn_home = _resolve_cairn_home(args)
    try:
        async with open_lifecycle_readonly(cairn_home) as store:
            record = await store.load(args.agent_id)
    except AgentNotFoundError:
        print(f"Unknown agent: {args.agent_id}", file=sys.stderr)
        return 1

    if record is None:
        print(f"Unknown agent: {args.agent_id}", file=sys.stderr)
        return 1

    payload = {
        "agent_id": record.agent_id,
        "state": record.state.value,
        "task": record.task,
        "error": record.error,
        "submission": record.submission,
        "files_written": record.files_written,
        "files_deleted": record.files_deleted,
        "claim_mismatch": record.claim_mismatch,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))

    # The mirror carries the ground-truth path lists for mismatch agents.
    run_written = getattr(record, "run_written", None)
    run_deleted = getattr(record, "run_deleted", None)
    if record.claim_mismatch:
        claimed = sorted(record.submission["changed_files"]) if record.submission else []
        actual = sorted((run_written or []) + (run_deleted or []))
        print(f"agent claims : {', '.join(claimed) if claimed else '(nothing)'}")
        print(f"actually wrote: {', '.join(actual) if actual else '(nothing)'}")
        print("! the agent's self-report does not match what it did", file=sys.stderr)
    return 0


async def _run_accept(args: argparse.Namespace) -> int:
    command = parse_command_payload("accept", {"agent_id": args.agent_id, "force": args.force})
    return await _dispatch_mutation(args, command)


async def _run_logs(args: argparse.Namespace) -> int:
    """Print an agent's sandbox run log (works for errored agents too)."""
    cairn_home = _resolve_cairn_home(args)
    try:
        async with open_lifecycle_readonly(cairn_home) as store:
            record = await store.load(args.agent_id)
    except AgentNotFoundError:
        print(f"Unknown agent: {args.agent_id}", file=sys.stderr)
        return 1
    if record is None:
        print(f"Unknown agent: {args.agent_id}", file=sys.stderr)
        return 1
    run_log = record.run_log
    if not run_log:
        print(f"No run log for {args.agent_id}", file=sys.stderr)
        return 1
    print(run_log)
    return 0


async def _run_undo(args: argparse.Namespace) -> int:
    """Undo a previously accepted agent's changes to the working tree."""
    command = parse_command_payload("undo", {"agent_id": args.agent_id})
    return await _dispatch_mutation(args, command)


async def _run_reject(args: argparse.Namespace) -> int:
    command = parse_command_payload("reject", {"agent_id": args.agent_id})
    return await _dispatch_mutation(args, command)


async def _run_inline(args: argparse.Namespace) -> int:
    """Run a single task to completion in this process, then exit."""
    path_settings, orchestrator_settings, executor_settings = _resolve_settings(args)
    cairn_home = _resolve_cairn_home(args)
    if read_daemon_pid(cairn_home) is not None:
        print("A daemon is running; use 'cairn queue' instead.", file=sys.stderr)
        return 2

    provider = _resolve_provider(args)
    orchestrator = CairnOrchestrator(
        project_root=path_settings.project_root or ".",
        cairn_home=cairn_home,
        config=orchestrator_settings,
        executor_settings=executor_settings,
        code_provider=provider,
    )
    await orchestrator.initialize()
    try:
        agent_id = await orchestrator.spawn_agent(args.task, TaskPriority.HIGH)
        record = await orchestrator.wait_for_agent(agent_id, timeout=args.timeout)
        print(json.dumps({"agent_id": agent_id, "state": record.state.value}, indent=2))
        return 0 if record.state is AgentState.REVIEWING else 1
    finally:
        await orchestrator.shutdown()


def _agentfs_dir(args: argparse.Namespace) -> Path:
    path_settings = PathsSettings(
        project_root=Path(args.project_root) if args.project_root is not None else None,
    )
    return (path_settings.project_root or Path(".")).resolve() / ".agentfs"


def _workspace_path(args: argparse.Namespace, name: str) -> Path:
    return _agentfs_dir(args) / f"{name}.db"


MANAGED_WORKSPACE_NAMES: frozenset[str] = frozenset({"stable", "bin"})


def _validate_workspace_name(name: str) -> None:
    """Reject traversal and Cairn-managed metadata names (review §2.8)."""
    if not name or any(part in ("", ".", "..") for part in name.split("/")) or "/" in name or "\\" in name:
        raise SystemExit(f"invalid workspace name: {name!r} (no path separators or traversal)")
    if not all(ch.isalnum() or ch in "_-" for ch in name):
        raise SystemExit(f"invalid workspace name: {name!r} (alphanumeric, dash, underscore only)")
    if name in MANAGED_WORKSPACE_NAMES or name.startswith(("agent-", "bin-")):
        raise SystemExit(f"workspace {name!r} is managed by Cairn and cannot be modified directly")


async def _run_workspace_create(args: argparse.Namespace) -> int:
    _validate_workspace_name(args.name)
    agentfs_dir = _agentfs_dir(args)
    agentfs_dir.mkdir(parents=True, exist_ok=True)
    workspace_path = agentfs_dir / f"{args.name}.db"
    if workspace_path.exists():
        print(f"Workspace '{args.name}' already exists at {workspace_path}", file=sys.stderr)
        return 1
    from fsdantic import Fsdantic

    workspace = await Fsdantic.open(path=str(workspace_path))
    await workspace.close()
    print(f"created workspace: {args.name} ({workspace_path})")
    return 0


async def _run_workspace_list(args: argparse.Namespace) -> int:
    agentfs_dir = _agentfs_dir(args)
    if not agentfs_dir.is_dir():
        print("No .agentfs directory found")
        return 0
    workspaces = sorted(agentfs_dir.glob("*.db"))
    if not workspaces:
        print("No workspaces found")
        return 0
    for ws_path in workspaces:
        size_mb = ws_path.stat().st_size / (1024 * 1024)
        print(f"{ws_path.stem}\t{ws_path}\t{size_mb:.2f} MB")
    return 0


async def _run_workspace_info(args: argparse.Namespace) -> int:
    _validate_workspace_name(args.name)
    workspace_path = _workspace_path(args, args.name)
    if not workspace_path.exists():
        print(f"Workspace '{args.name}' not found", file=sys.stderr)
        return 1
    from fsdantic import Fsdantic

    workspace = await Fsdantic.open(path=str(workspace_path), readonly=True)
    try:
        files = await workspace.files.search("**/*")
        kv_entries = await workspace.kv.list(prefix="")
        print(f"Name:    {args.name}")
        print(f"Path:    {workspace_path}")
        print(f"Size:    {workspace_path.stat().st_size / (1024 * 1024):.2f} MB")
        print(f"Files:   {len(files)}")
        print(f"KV:      {len(kv_entries)}")
    finally:
        await workspace.close()
    return 0


async def _run_workspace_delete(args: argparse.Namespace) -> int:
    _validate_workspace_name(args.name)
    workspace_path = _workspace_path(args, args.name)
    if not workspace_path.exists():
        print(f"Workspace '{args.name}' not found", file=sys.stderr)
        return 1
    workspace_path.unlink()
    print(f"deleted workspace: {args.name}")
    return 0


async def _open_workspace_readonly(args: argparse.Namespace, name: str, *, for_write: bool = False):
    """Open a user workspace; managed names are always refused."""
    _validate_workspace_name(name)
    workspace_path = _workspace_path(args, name)
    if not workspace_path.exists():
        raise SystemExit(f"workspace '{name}' not found")
    from fsdantic import Fsdantic

    return await Fsdantic.open(path=str(workspace_path), readonly=not for_write), workspace_path


async def _run_files_list(args: argparse.Namespace) -> int:
    ws, _ = await _open_workspace_readonly(args, args.workspace)
    try:
        if args.recursive:
            pattern = f"{args.path.rstrip('/')}/**/*" if args.path != "/" else "**/*"
            files = sorted(await ws.files.search(pattern))
            if not files:
                print(f"No files found in {args.path}")
                return 0
            for file_path in files:
                print(file_path)
            return 0
        entries = await ws.files.list_dir(args.path, output="full")
        if not entries:
            print(f"No files found in {args.path}")
            return 0
        for entry in entries:
            name = entry.get("name") if isinstance(entry, dict) else entry
            etype = entry.get("type", "file") if isinstance(entry, dict) else "file"
            print(f"{name}\t{etype}")
        return 0
    finally:
        await ws.close()


async def _run_files_read(args: argparse.Namespace) -> int:
    ws, _ = await _open_workspace_readonly(args, args.workspace)
    try:
        mode = "binary" if args.binary else "text"
        content = await ws.files.read(args.path, mode=mode)
        if args.binary:
            print(f"binary content ({len(content)} bytes)")
            if isinstance(content, bytes):
                print(content[:200])
            else:
                print(content)
        else:
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
            print(text)
        return 0
    except Exception as exc:  # noqa: BLE001 - surface the read error to the user
        print(f"error reading file: {exc}", file=sys.stderr)
        return 1
    finally:
        await ws.close()


async def _run_files_write(args: argparse.Namespace) -> int:
    ws, _ = await _open_workspace_readonly(args, args.workspace, for_write=True)
    try:
        mode = "binary" if args.binary else "text"
        content = args.content.encode() if args.binary else args.content
        await ws.files.write(args.path, content, mode=mode)
        print(f"written to {args.workspace}:{args.path}")
        return 0
    except Exception as exc:  # noqa: BLE001 - surface the write error to the user
        print(f"error writing file: {exc}", file=sys.stderr)
        return 1
    finally:
        await ws.close()


async def _run_files_search(args: argparse.Namespace) -> int:
    ws, _ = await _open_workspace_readonly(args, args.workspace)
    try:
        files = sorted(await ws.files.search(args.pattern))
        if not files:
            print(f"No files found matching '{args.pattern}'")
            return 0
        for file_path in files:
            print(file_path)
        return 0
    finally:
        await ws.close()


async def _run_files_tree(args: argparse.Namespace) -> int:
    ws, _ = await _open_workspace_readonly(args, args.workspace)
    try:
        tree_data = await ws.files.tree(args.path, max_depth=args.max_depth)

        def _render(node: dict, prefix: str = "") -> None:
            children = node.get("children", [])
            for child in children:
                name = child.get("name", "?")
                if child.get("type") == "directory":
                    print(f"{prefix}{name}/")
                    _render(child, prefix + "  ")
                else:
                    print(f"{prefix}{name}")

        _render(tree_data)
        return 0
    finally:
        await ws.close()


async def _run_preview_changes(args: argparse.Namespace) -> int:
    """Diff the agent's disposable workspace against the current tree."""
    from cairn.runtime import repo

    home = _resolve_cairn_home(args)
    workdir = home / "workspaces" / args.agent_id
    if not workdir.is_dir():
        print(f"workspace not found for agent: {args.agent_id}", file=sys.stderr)
        return 1
    project_root = (
        PathsSettings(project_root=Path(args.project_root) if args.project_root else None).project_root or Path(".")
    ).resolve()
    base = await asyncio.to_thread(repo.capture_manifest, project_root)
    current = await asyncio.to_thread(repo.capture_manifest, workdir)
    diff = repo.diff_manifests(base, current)
    rows = [
        (t, rel)
        for t, rels in (
            ("added", diff.added),
            ("modified", diff.modified),
            ("removed", diff.removed),
            ("mode", diff.mode_changed),
        )
        for rel in rels
    ]
    if not rows:
        print(f"No changes found for agent {args.agent_id}")
        return 0
    for change_type, rel in rows:
        print(f"{change_type:10} {rel}")
    print(f"\ntotal changes: {len(rows)}")
    return 0


async def _run_preview_file(args: argparse.Namespace) -> int:
    home = _resolve_cairn_home(args)
    workdir = home / "workspaces" / args.agent_id
    if not workdir.is_dir():
        print(f"workspace not found for agent: {args.agent_id}", file=sys.stderr)
        return 1
    try:
        print((workdir / args.file_path).read_text(encoding="utf-8"))
        return 0
    except Exception as exc:  # noqa: BLE001 - surface the read error to the user
        print(f"error reading file: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn")
    _add_common_flags(parser)

    subparsers = parser.add_subparsers(dest="command", required=True)

    up_parser = subparsers.add_parser("up", help="Start orchestrator service")
    up_parser.set_defaults(handler=_run_up, is_async=True)

    run_parser = subparsers.add_parser("run", help="Run a task inline to completion (no daemon)")
    run_parser.add_argument("task")
    run_parser.add_argument("--timeout", type=float, default=300.0, help="Seconds to wait for the agent")
    run_parser.set_defaults(handler=_run_inline, is_async=True)

    spawn_parser = subparsers.add_parser("spawn", help="Spawn a high-priority agent")
    spawn_parser.add_argument("task")
    spawn_parser.set_defaults(handler=_run_spawn, is_async=True)

    queue_parser = subparsers.add_parser("queue", help="Queue an agent task")
    queue_parser.add_argument("task")
    queue_parser.set_defaults(handler=_run_queue, is_async=True)

    list_parser = subparsers.add_parser("list-agents", help="List agents")
    list_parser.set_defaults(handler=_run_list_agents, is_async=True)

    status_parser = subparsers.add_parser("status", help="Show agent status")
    status_parser.add_argument("agent_id")
    status_parser.set_defaults(handler=_run_status, is_async=True)

    accept_parser = subparsers.add_parser("accept", help="Accept agent changes")
    accept_parser.add_argument("agent_id")
    accept_parser.add_argument(
        "--force", action="store_true", help="Accept even if the working tree changed since the agent started"
    )
    accept_parser.add_argument("--timeout", type=float, default=300.0, help="Seconds to wait for the accept to settle")
    accept_parser.set_defaults(handler=_run_accept, is_async=True)

    undo_parser = subparsers.add_parser("undo", help="Undo an accepted agent's changes to the working tree")
    undo_parser.add_argument("agent_id")
    undo_parser.set_defaults(handler=_run_undo, is_async=True)

    logs_parser = subparsers.add_parser("logs", help="Show an agent's sandbox run log")
    logs_parser.add_argument("agent_id")
    logs_parser.set_defaults(handler=_run_logs, is_async=True)

    reject_parser = subparsers.add_parser("reject", help="Reject agent changes")
    reject_parser.add_argument("agent_id")
    reject_parser.add_argument("--timeout", type=float, default=300.0, help="Seconds to wait for the reject to settle")
    reject_parser.set_defaults(handler=_run_reject, is_async=True)

    # --- workspace management (metadata workspaces only; managed names refused) ---
    workspace_parser = subparsers.add_parser("workspace", help="Workspace management")
    workspace_sub = workspace_parser.add_subparsers(dest="workspace_command", required=True)

    ws_create = workspace_sub.add_parser("create", help="Create a workspace")
    ws_create.add_argument("name")
    ws_create.set_defaults(handler=_run_workspace_create, is_async=True)

    ws_list = workspace_sub.add_parser("list", help="List workspaces")
    ws_list.set_defaults(handler=_run_workspace_list, is_async=True)

    ws_info = workspace_sub.add_parser("info", help="Show workspace info")
    ws_info.add_argument("name")
    ws_info.set_defaults(handler=_run_workspace_info, is_async=True)

    ws_delete = workspace_sub.add_parser("delete", help="Delete a workspace")
    ws_delete.add_argument("name")
    ws_delete.add_argument("--force", "-f", action="store_true", help="Skip confirmation")
    ws_delete.set_defaults(handler=_run_workspace_delete, is_async=True)

    # --- files (user workspaces only; managed names refused) ---
    files_parser = subparsers.add_parser("files", help="File operations in workspaces")
    files_sub = files_parser.add_subparsers(dest="files_command", required=True)

    fl = files_sub.add_parser("list", help="List files")
    fl.add_argument("workspace")
    fl.add_argument("--path", default="/")
    fl.add_argument("--recursive", "-r", action="store_true")
    fl.set_defaults(handler=_run_files_list, is_async=True)

    fr = files_sub.add_parser("read", help="Read a file")
    fr.add_argument("workspace")
    fr.add_argument("path")
    fr.add_argument("--binary", "-b", action="store_true")
    fr.set_defaults(handler=_run_files_read, is_async=True)

    fw = files_sub.add_parser("write", help="Write a file")
    fw.add_argument("workspace")
    fw.add_argument("path")
    fw.add_argument("content")
    fw.add_argument("--binary", "-b", action="store_true")
    fw.set_defaults(handler=_run_files_write, is_async=True)

    fs = files_sub.add_parser("search", help="Search files by glob")
    fs.add_argument("workspace")
    fs.add_argument("pattern")
    fs.set_defaults(handler=_run_files_search, is_async=True)

    ft = files_sub.add_parser("tree", help="Show a directory tree")
    ft.add_argument("workspace")
    ft.add_argument("--path", default="/")
    ft.add_argument("--max-depth", type=int, default=None)
    ft.set_defaults(handler=_run_files_tree, is_async=True)

    # --- preview (the review surface: disposable workspace vs current tree) ---
    preview_parser = subparsers.add_parser("preview", help="Preview agent changes")
    preview_sub = preview_parser.add_subparsers(dest="preview_command", required=True)

    pv = preview_sub.add_parser("changes", help="Diff the agent's workspace against the tree")
    pv.add_argument("agent_id")
    pv.set_defaults(handler=_run_preview_changes, is_async=True)

    pf = preview_sub.add_parser("file", help="Read a file from the agent's workspace")
    pf.add_argument("agent_id")
    pf.add_argument("file_path")
    pf.set_defaults(handler=_run_preview_file, is_async=True)

    # The common flags must work on every subcommand (docs promise
    # `--project-root`/`--cairn-home`/provider flags on all commands).
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                _add_common_flags_recursive(_as_parser(sub))

    return parser  # type: ignore[no-any-return]


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--cairn-home", default=None)
    parser.add_argument("--max-concurrent-agents", type=int, default=None)
    parser.add_argument("--max-execution-time", type=float, default=None)
    parser.add_argument("--max-memory-bytes", type=int, default=None)
    parser.add_argument("--max-recursion-depth", type=int, default=None)
    parser.add_argument("--provider", default="file", help="Code provider (file, inline, or plugin)")
    parser.add_argument("--provider-base-path", default=None, help="Base path for file provider")


def _add_common_flags_recursive(parser: argparse.ArgumentParser) -> None:
    _add_common_flags(parser)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                _add_common_flags_recursive(_as_parser(sub))


def _as_parser(value: object) -> argparse.ArgumentParser:
    from typing import cast

    return cast(argparse.ArgumentParser, value)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.is_async:
        return asyncio.run(args.handler(args))
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
