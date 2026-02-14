"""Core Cairn orchestrator for agent lifecycle management."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from agentfs_pydantic import AgentFSOptions
from agentfs_sdk import AgentFS

from cairn.agent import AgentContext, AgentState
from cairn.commands import (
    AcceptCommand,
    CairnCommand,
    CommandResult,
    CommandType,
    ListAgentsCommand,
    QueueCommand,
    RejectCommand,
    StatusCommand,
)
from cairn.code_generator import CodeGenerator
from cairn.executor import AgentExecutor
from cairn.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.external_functions import create_external_functions
from cairn.kv_models import LifecycleRecord
from cairn.kv_store import KVRepository
from cairn.lifecycle import LifecycleStore
from cairn.queue import TaskPriority, TaskQueue
from cairn.signals import SignalHandler
from cairn.watcher import FileWatcher
from cairn.workspace import WorkspaceMaterializer

class CairnOrchestrator:
    """Main orchestrator managing agent lifecycle."""

    def __init__(
        self,
        project_root: Path | str = ".",
        cairn_home: Path | str | None = None,
        config: OrchestratorSettings | None = None,
        code_generator: CodeGenerator | None = None,
        executor: AgentExecutor | None = None,
        external_functions_factory: Callable[[str, AgentFS, AgentFS, Any], dict[str, Any]]
        | None = None,
    ):
        path_settings = PathsSettings()
        self.project_root = Path(path_settings.project_root or project_root).resolve()
        self.agentfs_dir = self.project_root / ".agentfs"
        resolved_cairn_home = path_settings.cairn_home or cairn_home or Path.home() / ".cairn"
        self.cairn_home = Path(resolved_cairn_home).expanduser()
        self.config = config or OrchestratorSettings()

        self.stable: AgentFS | None = None
        self.bin: AgentFS | None = None
        self.active_agents: dict[str, AgentContext] = {}
        self.queue = TaskQueue()
        self._worker_task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_agents)
        self._running_tasks: set[asyncio.Task[None]] = set()

        self.llm = code_generator or CodeGenerator()
        self.executor = executor or AgentExecutor(settings=ExecutorSettings())
        self.external_functions_factory = external_functions_factory or create_external_functions

        self.watcher: FileWatcher | None = None
        self.signals: SignalHandler | None = None
        self.materializer: WorkspaceMaterializer | None = None
        self.lifecycle: LifecycleStore | None = None
        self.state_file = self.cairn_home / "state" / "orchestrator.json"

    async def initialize(self) -> None:
        """Initialize orchestrator directories and AgentFS instances."""
        self.agentfs_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("workspaces", "signals", "state"):
            (self.cairn_home / directory).mkdir(parents=True, exist_ok=True)

        self.stable = await AgentFS.open(
            AgentFSOptions(path=str(self.agentfs_dir / "stable.db")).model_dump()
        )
        self.bin = await AgentFS.open(
            AgentFSOptions(path=str(self.agentfs_dir / "bin.db")).model_dump()
        )

        self.watcher = FileWatcher(self.project_root, self.stable)
        self.signals = SignalHandler(
            self.cairn_home,
            self,
            enable_polling=self.config.enable_signal_polling,
        )
        self.materializer = WorkspaceMaterializer(self.cairn_home, stable_fs=self.stable)
        self.lifecycle = LifecycleStore(self.bin)

        await self.recover_from_lifecycle_store()

        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
        await self.persist_state()

    async def recover_from_lifecycle_store(self) -> None:
        """Rebuild active_agents from lifecycle store on restart.

        This is the single recovery path that ensures consistency after
        orchestrator restarts.
        """
        if self.lifecycle is None:
            return

        active_records = await self.lifecycle.list_active()

        for record in active_records:
            agent_id = record.agent_id
            db_path = Path(record.db_path)

            if not db_path.exists():
                record.state = AgentState.ERRORED
                record.error = "Agent DB missing after restart"
                record.state_changed_at = time.time()
                await self.lifecycle.save(record)
                continue

            try:
                agent_fs = await AgentFS.open(AgentFSOptions(path=str(db_path)).model_dump())
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
        """Run orchestrator service loops."""
        if self.watcher is None or self.signals is None:
            await self.initialize()

        assert self.watcher is not None
        assert self.signals is not None

        await asyncio.gather(
            self.watcher.watch(),
            self.signals.watch(),
        )

    async def submit_command(self, command: CairnCommand) -> CommandResult:
        """Dispatch normalized command objects to orchestrator handlers."""
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
                payload={
                    "state": ctx.state.value,
                    "task": ctx.task,
                    "error": ctx.error,
                    "submission": ctx.submission,
                },
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
        agents_dict = {}

        for agent_id, ctx in self.active_agents.items():
            agents_dict[agent_id] = {
                "state": ctx.state.value,
                "task": ctx.task,
                "priority": int(ctx.priority),
            }

        if self.lifecycle is not None:
            all_records = await self.lifecycle.list_all()
            for record in all_records:
                if record.agent_id not in agents_dict:
                    agents_dict[record.agent_id] = {
                        "state": record.state.value,
                        "task": record.task,
                        "priority": record.priority,
                    }

        return CommandResult(
            command_type=command.type,
            payload={"agents": agents_dict},
        )

    async def spawn_agent(
        self,
        task: str,
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> str:
        """Spawn and enqueue a new agent task."""
        if self.stable is None or self.lifecycle is None:
            raise RuntimeError("Orchestrator not initialized")

        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        agent_db = self.agentfs_dir / f"{agent_id}.db"
        agent_fs = await AgentFS.open(AgentFSOptions(path=str(agent_db)).model_dump())

        ctx = AgentContext(
            agent_id=agent_id,
            task=task,
            priority=priority,
            state=AgentState.QUEUED,
            agent_fs=agent_fs,
        )
        self.active_agents[agent_id] = ctx

        await self._save_lifecycle_record(ctx)
        await self.queue.enqueue(agent_id, priority)
        await self.persist_state()
        return agent_id

    async def accept_agent(self, agent_id: str) -> None:
        """Accept agent changes and merge overlay content into stable."""
        ctx = self._get_agent(agent_id)
        ctx.transition(AgentState.ACCEPTED)
        await self._save_lifecycle_record(ctx)

        if self.stable is None:
            raise RuntimeError("Stable AgentFS not initialized")

        await self._merge_overlay_to_stable(ctx.agent_fs, self.stable)
        await self.trash_agent(agent_id)
        await self.persist_state()

    async def reject_agent(self, agent_id: str) -> None:
        """Reject agent changes and cleanup overlay."""
        ctx = self._get_agent(agent_id)
        ctx.transition(AgentState.REJECTED)
        await self._save_lifecycle_record(ctx)
        await self.trash_agent(agent_id)
        await self.persist_state()

    async def trash_agent(self, agent_id: str) -> None:
        """Move agent to bin and cleanup runtime artifacts.

        This method is linear and idempotent - it can be called multiple
        times safely and will only perform necessary cleanup.
        """
        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            return

        await ctx.agent_fs.close()

        agent_db = self.agentfs_dir / f"{agent_id}.db"
        bin_db = self.agentfs_dir / f"bin-{agent_id}.db"

        if agent_db.exists() and not bin_db.exists():
            shutil.move(agent_db, bin_db)

        if self.lifecycle is not None:
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
            await self.lifecycle.save(record)

        if self.materializer is not None:
            await self.materializer.cleanup(agent_id)

        self.active_agents.pop(agent_id, None)
        await self.persist_state()

    async def _worker_loop(self) -> None:
        """Continuously dispatch queued tasks through a semaphore-gated runner."""
        while True:
            queued = await self.queue.dequeue_wait()
            agent_id = queued.task
            await self._semaphore.acquire()
            task = asyncio.create_task(self._run_agent(agent_id))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _run_agent(self, agent_id: str) -> None:
        """Run one agent through generation/execution/submission lifecycle."""
        ctx = self.active_agents.get(agent_id)

        try:
            if ctx is None:
                return

            async def transition(new_state: AgentState) -> None:
                ctx.transition(new_state)
                await self._save_lifecycle_record(ctx)
                await self.persist_state()

            await transition(AgentState.SPAWNING)
            await transition(AgentState.GENERATING)
            generated = await self.llm.generate(ctx.task)
            ctx.generated_code = generated

            is_valid, error = self.executor.validate_code(generated)
            if not is_valid:
                raise RuntimeError(error or "generated code failed validation")

            if self.stable is None:
                raise RuntimeError("Stable AgentFS not initialized")

            await transition(AgentState.EXECUTING)
            functions = self.external_functions_factory(agent_id, ctx.agent_fs, self.stable, self.llm)
            execution_result = await self.executor.execute(generated, functions, agent_id)
            ctx.execution_result = execution_result
            if execution_result.failed:
                raise RuntimeError(execution_result.error or "execution failed")

            await transition(AgentState.SUBMITTING)
            submission_repo = KVRepository(ctx.agent_fs)
            ctx.submission = await submission_repo.load_submission(ctx.agent_id)

            if self.materializer is not None:
                await self.materializer.materialize(agent_id, ctx.agent_fs)

            await transition(AgentState.REVIEWING)
        except Exception as exc:
            ctx.error = str(exc)
            await transition(AgentState.ERRORED)
        finally:
            self._semaphore.release()
            await self.persist_state()

    async def persist_state(self) -> None:
        """Persist queue stats snapshot for CLI consumers.

        Agent metadata is now stored in the lifecycle store (bin.db KV),
        so this only writes queue statistics.
        """
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
                    if ctx.state
                    in {
                        AgentState.SPAWNING,
                        AgentState.GENERATING,
                        AgentState.EXECUTING,
                        AgentState.SUBMITTING,
                    }
                ),
            },
        }
        self.state_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    async def cleanup_completed_agents(self, max_age_seconds: float = 86400 * 7) -> int:
        """Remove lifecycle records and DBs for old completed agents.

        This is the single retention policy location for the system.

        Args:
            max_age_seconds: Maximum age in seconds for completed agents (default: 7 days)

        Returns:
            Number of agents cleaned up
        """
        if self.lifecycle is None:
            return 0

        return await self.lifecycle.cleanup_old(max_age_seconds, self.agentfs_dir)

    async def _save_lifecycle_record(self, ctx: AgentContext) -> None:
        """Save agent context to canonical lifecycle store."""
        if self.lifecycle is None:
            return

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
        await self.lifecycle.save(record)

    def _get_agent(self, agent_id: str) -> AgentContext:
        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            raise KeyError(f"Unknown agent_id: {agent_id}")
        return ctx

    async def _merge_overlay_to_stable(self, source: AgentFS, target: AgentFS, src_path: str = "/") -> None:
        """Copy source overlay files into stable AgentFS recursively."""
        for base in (src_path, src_path.lstrip("/") or "."):
            try:
                entries = await source.fs.readdir(base)
                break
            except FileNotFoundError:
                entries = []
        for entry in entries:
            name = getattr(entry, "name", None)
            if not name:
                continue

            source_child = f"{src_path.rstrip('/')}/{name}" if src_path != "/" else f"/{name}"
            source_child_rel = source_child.lstrip("/")
            if self._is_directory_entry(entry):
                await self._merge_overlay_to_stable(source, target, source_child)
                continue

            file_bytes = await source.fs.read_file(source_child)
            await target.fs.write_file(source_child_rel, file_bytes)

    def _is_directory_entry(self, entry: Any) -> bool:
        entry_type = getattr(entry, "type", None)
        return bool(
            entry_type == "directory"
            or entry_type == "dir"
            or getattr(entry, "is_directory", False)
            or getattr(entry, "is_dir", False)
        )
