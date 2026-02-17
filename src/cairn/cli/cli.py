"""Command-line interface for the Cairn orchestrator service."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from cairn.cli.commands import (
    AcceptCommand,
    CairnCommand,
    CommandResult,
    ListAgentsCommand,
    QueueCommand,
    RejectCommand,
    StatusCommand,
    parse_command_payload,
)
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import CodeProvider, resolve_code_provider
from cairn.orchestrator.queue import TaskPriority
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


def _resolve_provider(args: argparse.Namespace, project_root: Path | None) -> CodeProvider:
    base_path = Path(args.provider_base_path) if args.provider_base_path else None
    return resolve_code_provider(
        args.provider,
        project_root=project_root,
        base_path=base_path,
    )


async def _run_up(args: argparse.Namespace) -> int:
    path_settings, orchestrator_settings, executor_settings = _resolve_settings(args)
    provider = _resolve_provider(args, path_settings.project_root)
    orchestrator = CairnOrchestrator(
        project_root=path_settings.project_root or ".",
        cairn_home=path_settings.cairn_home,
        config=orchestrator_settings,
        executor_settings=executor_settings,
        code_provider=provider,
    )
    await orchestrator.initialize()
    await orchestrator.run()
    return 0


class CairnCommandClient:
    """Submit CLI commands through orchestrator command handling."""

    def __init__(
        self,
        *,
        path_settings: PathsSettings,
        orchestrator_settings: OrchestratorSettings,
        executor_settings: ExecutorSettings,
        provider: CodeProvider,
    ) -> None:
        self.path_settings = path_settings
        self.orchestrator_settings = orchestrator_settings
        self.executor_settings = executor_settings
        self.provider = provider

    async def submit(self, command: CairnCommand) -> CommandResult:
        orchestrator = CairnOrchestrator(
            project_root=self.path_settings.project_root or ".",
            cairn_home=self.path_settings.cairn_home,
            config=self.orchestrator_settings,
            executor_settings=self.executor_settings,
            code_provider=self.provider,
        )
        await orchestrator.initialize()
        return await orchestrator.submit_command(command)


async def _submit_command(args: argparse.Namespace, command: CairnCommand) -> CommandResult:
    path_settings, orchestrator_settings, executor_settings = _resolve_settings(args)
    provider = _resolve_provider(args, path_settings.project_root)
    client = CairnCommandClient(
        path_settings=path_settings,
        orchestrator_settings=orchestrator_settings,
        executor_settings=executor_settings,
        provider=provider,
    )

    match command:
        case QueueCommand() | AcceptCommand() | RejectCommand() | StatusCommand() | ListAgentsCommand():
            return await client.submit(command)

    raise ValueError(f"unsupported command type: {command.type.value}")


async def _run_spawn(args: argparse.Namespace) -> int:
    command = parse_command_payload("spawn", {"task": args.task, "priority": int(TaskPriority.HIGH)})
    await _submit_command(args, command)
    print("queued spawn request")
    return 0


async def _run_queue(args: argparse.Namespace) -> int:
    command = parse_command_payload("queue", {"task": args.task, "priority": int(TaskPriority.NORMAL)})
    await _submit_command(args, command)
    print("queued task request")
    return 0


async def _run_list_agents(args: argparse.Namespace) -> int:
    command = parse_command_payload("list_agents", {})
    result = await _submit_command(args, command)
    agents = result.payload.get("agents", {})
    if not agents:
        print("No active agents")
        return 0

    for agent_id, agent in sorted(agents.items()):
        print(f"{agent_id}\t{agent.get('state')}\t{agent.get('task')}")
    return 0


async def _run_status(args: argparse.Namespace) -> int:
    command = parse_command_payload("status", {"agent_id": args.agent_id})
    try:
        result = await _submit_command(args, command)
    except ValueError:
        print(f"Unknown agent: {args.agent_id}")
        return 1

    print(json.dumps(result.payload, indent=2, sort_keys=True))
    return 0


async def _run_accept(args: argparse.Namespace) -> int:
    command = parse_command_payload("accept", {"agent_id": args.agent_id})
    await _submit_command(args, command)
    print(f"queued accept for {args.agent_id}")
    return 0


async def _run_reject(args: argparse.Namespace) -> int:
    command = parse_command_payload("reject", {"agent_id": args.agent_id})
    await _submit_command(args, command)
    print(f"queued reject for {args.agent_id}")
    return 0


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

    spawn_parser = subparsers.add_parser("spawn", help="Spawn an agent")
    spawn_parser.add_argument("task")
    spawn_parser.set_defaults(handler=_run_spawn, is_async=True)

    queue_parser = subparsers.add_parser("queue", help="Queue an agent task")
    queue_parser.add_argument("task")
    queue_parser.set_defaults(handler=_run_queue, is_async=True)

    list_parser = subparsers.add_parser("list-agents", help="List active agents")
    list_parser.set_defaults(handler=_run_list_agents, is_async=True)

    status_parser = subparsers.add_parser("status", help="Show agent status")
    status_parser.add_argument("agent_id")
    status_parser.set_defaults(handler=_run_status, is_async=True)

    accept_parser = subparsers.add_parser("accept", help="Accept agent changes")
    accept_parser.add_argument("agent_id")
    accept_parser.set_defaults(handler=_run_accept, is_async=True)

    reject_parser = subparsers.add_parser("reject", help="Reject agent changes")
    reject_parser.add_argument("agent_id")
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
