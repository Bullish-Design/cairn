"""Command-line interface for the Cairn orchestrator service.

The daemon owns the databases.  The CLI writes signals for mutating commands
and reads the lifecycle store read-only for queries, so no subcommand ever
constructs a full orchestrator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from cairn.cli.commands import (
    CairnCommand,
    parse_command_payload,
)
from cairn.core.exceptions import AgentNotFoundError
from cairn.core.exceptions import TimeoutError as CairnTimeoutError
from cairn.orchestrator.daemon import daemon_pidfile, read_daemon_pid
from cairn.orchestrator.lifecycle import LifecycleRecord, open_lifecycle_readonly
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.signals import write_signal
from cairn.providers.providers import CodeProvider, resolve_code_provider
from cairn.orchestrator.queue import TaskPriority
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
            enable_signal_polling=(
                args.enable_signal_polling
                if args.enable_signal_polling is not None
                else orchestrator_settings.enable_signal_polling
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
    with daemon_pidfile(cairn_home):
        orchestrator = CairnOrchestrator(
            project_root=path_settings.project_root or ".",
            cairn_home=cairn_home,
            config=orchestrator_settings,
            executor_settings=executor_settings,
            code_provider=provider,
        )
        await orchestrator.initialize()
        try:
            await orchestrator.run()
        finally:
            await orchestrator.shutdown()
    return 0


async def _dispatch_mutation(args: argparse.Namespace, command: CairnCommand) -> int:
    cairn_home = _resolve_cairn_home(args)

    if read_daemon_pid(cairn_home) is None:
        print(
            "No Cairn daemon is running.\n"
            "  Start one with:  cairn up\n"
            "  Or run this task inline with:  cairn run <task>",
            file=sys.stderr,
        )
        return 2

    path = write_signal(cairn_home, command)
    print(f"submitted {command.type.value} ({path.name})")
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
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


async def _poll_until(
    args: argparse.Namespace,
    agent_id: str,
    states: set[AgentState],
    *,
    timeout: float,
) -> LifecycleRecord:
    """Poll the read-only lifecycle store until the agent settles."""
    cairn_home = _resolve_cairn_home(args)
    deadline = time.monotonic() + timeout
    async with open_lifecycle_readonly(cairn_home) as store:
        while True:
            record = await store.load(agent_id)
            if record is not None and record.state in states:
                return record
            if time.monotonic() >= deadline:
                raise CairnTimeoutError(
                    f"Agent {agent_id} did not reach {[s.value for s in states]} within {timeout}s",
                    error_code="AGENT_WAIT_TIMEOUT",
                    context={"agent_id": agent_id, "timeout_seconds": timeout},
                )
            await asyncio.sleep(0.1)


async def _run_accept(args: argparse.Namespace) -> int:
    command = parse_command_payload("accept", {"agent_id": args.agent_id})
    rc = await _dispatch_mutation(args, command)
    if rc != 0:
        return rc
    record = await _poll_until(args, args.agent_id, {AgentState.ACCEPTED, AgentState.ERRORED}, timeout=args.timeout)
    if record.state is AgentState.ERRORED:
        print(f"accept failed: {record.error}", file=sys.stderr)
        return 1
    stats = record.accept_stats or {}
    print(
        f"accepted {args.agent_id}: "
        f"{stats.get('files_merged', 0)} file(s) merged, "
        f"{stats.get('tombstones_applied', 0)} deletion(s) applied"
    )
    return 0


async def _run_reject(args: argparse.Namespace) -> int:
    command = parse_command_payload("reject", {"agent_id": args.agent_id})
    rc = await _dispatch_mutation(args, command)
    if rc != 0:
        return rc
    record = await _poll_until(args, args.agent_id, {AgentState.REJECTED, AgentState.ERRORED}, timeout=args.timeout)
    if record.state is AgentState.ERRORED:
        print(f"reject failed: {record.error}", file=sys.stderr)
        return 1
    print(f"rejected {args.agent_id}")
    return 0


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
    parser.add_argument("--enable-signal-polling", action=argparse.BooleanOptionalAction, default=None)
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
    accept_parser.add_argument("--timeout", type=float, default=300.0, help="Seconds to wait for the accept to settle")
    accept_parser.set_defaults(handler=_run_accept, is_async=True)

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
