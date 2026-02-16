"""Core Cairn orchestrator for agent lifecycle management."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fsdantic import Fsdantic, MergeStrategy, Workspace
import grail

from cairn.agent import AgentContext, AgentState
from cairn.external_functions import create_external_functions
from cairn.providers import CodeProvider, CodeProviderError, FileCodeProvider
from cairn.commands import (
    AcceptCommand,
    CairnCommand,
    CommandResult,
    ListAgentsCommand,
    QueueCommand,
    RejectCommand,
    StatusCommand,
)
from cairn.lifecycle import LifecycleRecord, LifecycleStore, SUBMISSION_KEY, SubmissionRecord
from cairn.queue import TaskPriority, TaskQueue
from cairn.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.signals import SignalHandler
from cairn.watcher import FileWatcher


def _load_grail_script(pym_path: Path) -> Any:
    """Load a Grail script using legacy and current loader entry points."""

    script_path = str(pym_path)

    # Grail 1.x exposed a top-level `load` function.
    legacy_loader = getattr(grail, "load", None)
    if callable(legacy_loader):
        return legacy_loader(script_path)

    # Grail 2.x loaders can vary by release; try known file-based entry points.
    candidate_loaders: tuple[tuple[str, str], ...] = (
        ("Script", "from_file"),
        ("Script", "load"),
        ("Program", "from_file"),
        ("Program", "load"),
    )
    for class_name, method_name in candidate_loaders:
        cls = getattr(grail, class_name, None)
        if cls is None:
            continue
        loader = getattr(cls, method_name, None)
        if callable(loader):
            return loader(script_path)

    available_attrs = ", ".join(sorted(name for name in dir(grail) if not name.startswith("_")))
    raise RuntimeError(
        "No supported Grail script loader found. Expected `grail.load` or a supported "
        "2.x loader (Script/Program from_file/load). "
        f"Available grail attributes: {available_attrs}"
    )


class CairnOrchestrator:
    """Main orchestrator managing agent lifecycle."""

    def __init__(
        self,
        project_root: Path | str = ".",
        cairn_home: Path | str | None = None,
        config: OrchestratorSettings | None = None,
        executor_settings: ExecutorSettings | None = None,
        code_provider: CodeProvider | None = None,
        tools_factory: Callable[[str, Workspace, Workspace], dict[str, Callable[..., Any]]] | None = None,
    ):
        path_settings = PathsSettings()
        self.project_root = Path(path_settings.project_root or project_root).resolve()
        self.agentfs_dir = self.project_root / ".agentfs"
        resolved_cairn_home = path_settings.cairn_home or cairn_home or Path.home() / ".cairn"
        self.cairn_home = Path(resolved_cairn_home).expanduser()
        self.config = config or OrchestratorSettings()
        self.executor_settings = executor_settings or ExecutorSettings()

        self.stable: Workspace | None = None
        self.bin: Workspace | None = None
        self.active_agents: dict[str, AgentContext] = {}
        self.queue = TaskQueue()
        self._worker_task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_agents)
        self._running_tasks: set[asyncio.Task[None]] = set()

        self.code_provider = code_provider or FileCodeProvider(base_path=self.project_root)
        self.tools_factory = tools_factory or create_external_functions

        self.watcher: FileWatcher | None = None
        self.signals: SignalHandler | None = None
        self.lifecycle: LifecycleStore | None = None
        self.state_file = self.cairn_home / "state" / "orchestrator.json"

    async def initialize(self) -> None:
        self.agentfs_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("workspaces", "signals", "state"):
            (self.cairn_home / directory).mkdir(parents=True, exist_ok=True)

        self.stable = await Fsdantic.open(path=str(self.agentfs_dir / "stable.db"))
        self.bin = await Fsdantic.open(path=str(self.agentfs_dir / "bin.db"))

        self.watcher = FileWatcher(self.project_root, self.stable)
        self.signals = SignalHandler(self.cairn_home, self, enable_polling=self.config.enable_signal_polling)
        self.lifecycle = LifecycleStore(self.bin)

        await self.recover_from_lifecycle_store()

        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
        await self.persist_state()

    async def recover_from_lifecycle_store(self) -> None:
        if self.lifecycle is None:
            return

        for record in await self.lifecycle.list_active():
            agent_id = record.agent_id
            db_path = Path(record.db_path)

            if not db_path.exists():
                record.state = AgentState.ERRORED
                record.error = "Agent DB missing after restart"
                record.state_changed_at = time.time()
                await self.lifecycle.save(record)
                continue

            try:
                agent_fs = await Fsdantic.open(path=str(db_path))
            except Exception as exc:
                record.state = AgentState.ERRORED
                record.error = f"Failed to open agent DB: {exc}"
                record.state_changed_at = time.time()
                await self.lifecycle.save(record)
                continue

            ctx = AgentContext(
                agent_id=agent_id,
                task=record.task,
                priority=TaskPriority(record.priority),
                state=record.state,
                agent_fs=agent_fs,
                created_at=record.created_at,
                state_changed_at=record.state_changed_at,
                submission=record.submission,
                error=record.error,
            )
            self.active_agents[agent_id] = ctx

            if ctx.state == AgentState.QUEUED:
                await self.queue.enqueue(agent_id, ctx.priority)

    async def run(self) -> None:
        assert self.watcher is not None
        assert self.signals is not None
        await asyncio.gather(self.watcher.watch(), self.signals.watch())

    async def submit_command(self, command: CairnCommand) -> CommandResult:
        match command:
            case QueueCommand():
                return await self._handle_queue(command)
            case AcceptCommand():
                return await self._handle_accept(command)
            case RejectCommand():
                return await self._handle_reject(command)
            case StatusCommand():
                return await self._handle_status(command)
            case ListAgentsCommand():
                return await self._handle_list_agents(command)
        raise ValueError(f"Unsupported command type: {command.type.value}")

    async def _handle_queue(self, command: QueueCommand) -> CommandResult:
        agent_id = await self.spawn_agent(task=command.task, priority=command.priority)
        return CommandResult(command_type=command.type, agent_id=agent_id)

    async def _handle_accept(self, command: AcceptCommand) -> CommandResult:
        await self.accept_agent(command.agent_id)
        return CommandResult(command_type=command.type, agent_id=command.agent_id)

    async def _handle_reject(self, command: RejectCommand) -> CommandResult:
        await self.reject_agent(command.agent_id)
        return CommandResult(command_type=command.type, agent_id=command.agent_id)

    async def _handle_status(self, command: StatusCommand) -> CommandResult:
        ctx = self.active_agents.get(command.agent_id)
        if ctx:
            return CommandResult(
                command_type=command.type,
                agent_id=ctx.agent_id,
                payload={"state": ctx.state.value, "task": ctx.task, "error": ctx.error, "submission": ctx.submission},
            )

        if self.lifecycle is None:
            raise KeyError(f"Unknown agent_id: {command.agent_id}")

        record = await self.lifecycle.load(command.agent_id)
        if record is None:
            raise KeyError(f"Unknown agent_id: {command.agent_id}")

        return CommandResult(
            command_type=command.type,
            agent_id=record.agent_id,
            payload={
                "state": record.state.value,
                "task": record.task,
                "error": record.error,
                "submission": record.submission,
            },
        )

    async def _handle_list_agents(self, command: ListAgentsCommand) -> CommandResult:
        agents_dict: dict[str, dict[str, Any]] = {
            agent_id: {"state": ctx.state.value, "task": ctx.task, "priority": int(ctx.priority)}
            for agent_id, ctx in self.active_agents.items()
        }

        if self.lifecycle is not None:
            for record in await self.lifecycle.list_all():
                if record.agent_id not in agents_dict:
                    agents_dict[record.agent_id] = {
                        "state": record.state.value,
                        "task": record.task,
                        "priority": record.priority,
                    }

        return CommandResult(command_type=command.type, payload={"agents": agents_dict})

    async def spawn_agent(self, task: str, priority: TaskPriority = TaskPriority.NORMAL) -> str:
        if self.lifecycle is None:
            raise RuntimeError("Orchestrator not initialized")

        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        agent_db = self.agentfs_dir / f"{agent_id}.db"
        agent_fs = await Fsdantic.open(path=str(agent_db))

        ctx = AgentContext(agent_id=agent_id, task=task, priority=priority, state=AgentState.QUEUED, agent_fs=agent_fs)
        self.active_agents[agent_id] = ctx

        await self._save_lifecycle_record(ctx)
        await self.queue.enqueue(agent_id, priority)
        await self.persist_state()
        return agent_id

    async def accept_agent(self, agent_id: str) -> None:
        ctx = self._get_agent(agent_id)
        if ctx.state is not AgentState.REVIEWING:
            raise ValueError(f"Agent {agent_id} not in reviewing state")

        if self.stable is None:
            raise RuntimeError("Stable workspace not initialized")

        merge_result = await self.stable.overlay.merge(ctx.agent_fs, strategy=MergeStrategy.OVERWRITE)
        merge_errors = getattr(merge_result, "errors", None)
        if merge_errors:
            raise RuntimeError(f"Failed to merge agent overlay: {merge_errors}")

        ctx.transition(AgentState.ACCEPTED)
        await self._save_lifecycle_record(ctx)
        await self.trash_agent(agent_id)
        await self.persist_state()

    async def reject_agent(self, agent_id: str) -> None:
        ctx = self._get_agent(agent_id)
        if ctx.state is not AgentState.REVIEWING:
            raise ValueError(f"Agent {agent_id} not in reviewing state")

        ctx.transition(AgentState.REJECTED)
        await self._save_lifecycle_record(ctx)
        await self.trash_agent(agent_id)
        await self.persist_state()

    async def trash_agent(self, agent_id: str) -> None:
        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            return

        await ctx.agent_fs.close()

        agent_db = self.agentfs_dir / f"{agent_id}.db"
        bin_db = self.agentfs_dir / f"bin-{agent_id}.db"

        if agent_db.exists() and not bin_db.exists():
            shutil.move(agent_db, bin_db)

        if self.lifecycle is not None:
            # Load existing record to preserve version for optimistic concurrency control
            existing = await self.lifecycle.load(ctx.agent_id)

            record = LifecycleRecord(
                agent_id=ctx.agent_id,
                task=ctx.task,
                priority=int(ctx.priority),
                state=ctx.state,
                created_at=ctx.created_at,
                state_changed_at=ctx.state_changed_at,
                db_path=str(bin_db),
                submission=ctx.submission,
                error=ctx.error,
            )

            # Preserve version and timestamps from existing record to avoid version conflicts
            if existing:
                record.version = existing.version
                record.created_at = existing.created_at
                record.updated_at = existing.updated_at

            await self.lifecycle.save(record)

        workspace = self.cairn_home / "workspaces" / agent_id
        if workspace.exists():
            shutil.rmtree(workspace)

        self.active_agents.pop(agent_id, None)
        await self.persist_state()

    async def _worker_loop(self) -> None:
        while True:
            queued = await self.queue.dequeue_wait()
            agent_id = queued.task
            await self._semaphore.acquire()
            task = asyncio.create_task(self._run_agent(agent_id))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _run_agent(self, agent_id: str) -> None:
        ctx = self.active_agents.get(agent_id)

        try:
            if ctx is None:
                return

            async def transition(new_state: AgentState) -> None:
                ctx.transition(new_state)
                await self._save_lifecycle_record(ctx)
                await self.persist_state()

            await transition(AgentState.GENERATING)

            if self.stable is None:
                raise RuntimeError("Stable workspace not initialized")

            context = {"agent_id": ctx.agent_id, "workspace": ctx.agent_fs, "stable": self.stable}

            try:
                generated = await self.code_provider.get_code(ctx.task, context)
            except CodeProviderError as exc:
                ctx.error = str(exc)
                await transition(AgentState.ERRORED)
                return

            ctx.generated_code = generated
            is_valid, error = await self.code_provider.validate_code(generated)
            if not is_valid:
                ctx.error = error or "Code provider validation failed"
                await transition(AgentState.ERRORED)
                return

            await transition(AgentState.EXECUTING)
            tools = self.tools_factory(agent_id, ctx.agent_fs, self.stable)

            grail_dir = self.project_root / ".grail" / "agents" / ctx.agent_id
            grail_dir.mkdir(parents=True, exist_ok=True)
            pym_path = grail_dir / "task.pym"
            pym_path.write_text(generated, encoding="utf-8")

            # Support both legacy Grail `load` and newer 2.x file loader names.
            script = _load_grail_script(pym_path)
            check_result = script.check()
            check_payload = {
                "valid": bool(getattr(check_result, "valid", False)),
                "errors": [str(error) for error in (getattr(check_result, "errors", None) or [])],
            }
            (grail_dir / "check.json").write_text(
                json.dumps(check_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if not check_result.valid:
                ctx.error = self._format_grail_errors(check_result)
                await transition(AgentState.ERRORED)
                return

            await script.run(inputs={"task_description": ctx.task}, externals=tools)

            await transition(AgentState.SUBMITTING)
            submission_repo = ctx.agent_fs.kv.repository(prefix="", model_type=SubmissionRecord)
            submission_record = await submission_repo.load(SUBMISSION_KEY)
            ctx.submission = submission_record.submission if submission_record else None

            preview_dir = self.cairn_home / "workspaces" / agent_id
            await ctx.agent_fs.materialize.to_disk(
                target_path=preview_dir,
                base=self.stable,
                clean=True,
                allow_root=self.cairn_home / "workspaces",
            )

            await transition(AgentState.REVIEWING)
        except (grail.ExecutionError, grail.InputError) as exc:
            if ctx is not None:
                ctx.error = str(exc)
                ctx.transition(AgentState.ERRORED)
                await self._save_lifecycle_record(ctx)
        except Exception as exc:
            if ctx is not None:
                ctx.error = str(exc)
                ctx.transition(AgentState.ERRORED)
                await self._save_lifecycle_record(ctx)
        finally:
            self._semaphore.release()
            await self.persist_state()

    async def persist_state(self) -> None:
        state_dir = self.state_file.parent
        state_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "project_root": str(self.project_root),
            "updated_at": time.time(),
            "queue": {
                "pending": self.queue.size(),
                "running": sum(
                    1
                    for ctx in self.active_agents.values()
                    if ctx.state in {AgentState.GENERATING, AgentState.EXECUTING, AgentState.SUBMITTING}
                ),
            },
        }
        self.state_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    async def cleanup_completed_agents(self, max_age_seconds: float = 86400 * 7) -> int:
        if self.lifecycle is None:
            return 0
        return await self.lifecycle.cleanup_old(max_age_seconds, self.agentfs_dir)

    async def _save_lifecycle_record(self, ctx: AgentContext) -> None:
        if self.lifecycle is None:
            return

        # Load existing record to preserve version for optimistic concurrency control
        existing = await self.lifecycle.load(ctx.agent_id)

        db_path = self.agentfs_dir / f"{ctx.agent_id}.db"
        if not db_path.exists():
            db_path = self.agentfs_dir / f"bin-{ctx.agent_id}.db"

        record = LifecycleRecord(
            agent_id=ctx.agent_id,
            task=ctx.task,
            priority=int(ctx.priority),
            state=ctx.state,
            created_at=ctx.created_at,
            state_changed_at=ctx.state_changed_at,
            db_path=str(db_path),
            submission=ctx.submission,
            error=ctx.error,
        )

        # Preserve version and timestamps from existing record to avoid version conflicts
        if existing:
            record.version = existing.version
            record.created_at = existing.created_at
            record.updated_at = existing.updated_at

        await self.lifecycle.save(record)

    def _get_agent(self, agent_id: str) -> AgentContext:
        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            raise KeyError(f"Unknown agent_id: {agent_id}")
        return ctx

    @staticmethod
    def _format_grail_errors(check_result: Any) -> str:
        errors = getattr(check_result, "errors", None)
        if errors:
            return "Grail validation failed: " + "; ".join(str(error) for error in errors)
        return "Grail validation failed"
