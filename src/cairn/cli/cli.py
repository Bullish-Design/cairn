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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--cairn-home", default=None)
    parser.add_argument("--max-concurrent-agents", type=int, default=None)
    parser.add_argument("--max-execution-time", type=float, default=None)
    parser.add_argument("--max-memory-bytes", type=int, default=None)
    parser.add_argument("--max-recursion-depth", type=int, default=None)
    parser.add_argument("--provider", default="file", help="Code provider (file, inline, or plugin)")
    parser.add_argument("--provider-base-path", default=None, help="Base path for file provider")

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.is_async:
        return asyncio.run(args.handler(args))
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
