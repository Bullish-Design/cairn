"""Core Cairn orchestrator for agent lifecycle management."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from fsdantic import Fsdantic, MergeStrategy, Workspace

from cairn.cli.commands import (
    AcceptCommand,
    CairnCommand,
    CommandResult,
    ListAgentsCommand,
    QueueCommand,
    RejectCommand,
    StatusCommand,
)
from cairn.core.constants import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
    LIFECYCLE_MAX_RETRY_ATTEMPTS,
    LIFECYCLE_RETRY_BACKOFF_FACTOR,
    LIFECYCLE_RETRY_INITIAL_DELAY_SECONDS,
)
from cairn.core.exceptions import (
    CairnError,
    LifecycleError,
    ProviderError,
    RecoverableError,
    ResourceLimitError,
    VersionConflictError,
    WorkspaceMergeError,
)
from cairn.core.exceptions import (
    TimeoutError as CairnTimeoutError,
)
from cairn.core.types import AgentSummary
from cairn.orchestrator.lifecycle import SUBMISSION_KEY, LifecycleRecord, LifecycleStore, SubmissionRecord
from cairn.orchestrator.queue import TaskPriority, TaskQueue
from cairn.orchestrator.signals import SignalHandler
from cairn.providers.providers import CodeProvider, FileCodeProvider
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.sandbox import BwrapExecutor, SandboxExecutionError, SandboxExecutor, SandboxResult
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.runtime.workspace_cache import WorkspaceCache
from cairn.runtime.workspace_manager import WorkspaceManager
from cairn.utils.error_formatting import format_agent_error
from cairn.utils.retry_utils import with_retry
from cairn.watcher.watcher import FileWatcher

logger = logging.getLogger(__name__)


class CairnOrchestrator:
    """Main orchestrator managing agent lifecycle."""

    def __init__(
        self,
        project_root: Path | str = ".",
        cairn_home: Path | str | None = None,
        config: OrchestratorSettings | None = None,
        executor_settings: ExecutorSettings | None = None,
        code_provider: CodeProvider | None = None,
        executor_factory: Callable[..., SandboxExecutor] | None = None,
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
        self.queue = TaskQueue(max_size=self.config.max_queue_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_agents)
        self._running_tasks: set[asyncio.Task[None]] = set()

        self.code_provider = code_provider or FileCodeProvider(base_path=self.project_root)
        self.executor_factory = executor_factory or BwrapExecutor

        self.watcher: FileWatcher | None = None
        self.signals: SignalHandler | None = None
        self.lifecycle: LifecycleStore | None = None
        self.state_file = self.cairn_home / "state" / "orchestrator.json"
        self.workspace_manager = WorkspaceManager()
        self.workspace_cache = WorkspaceCache(max_size=self.config.workspace_cache_size)

    async def initialize(self) -> None:
        self.agentfs_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("workspaces", "signals", "state"):
            (self.cairn_home / directory).mkdir(parents=True, exist_ok=True)

        self.stable = await Fsdantic.open(path=str(self.agentfs_dir / "stable.db"))
        self.bin = await Fsdantic.open(path=str(self.agentfs_dir / "bin.db"))
        self.workspace_manager.track_workspace(self.stable)
        self.workspace_manager.track_workspace(self.bin)

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
                record.error = format_agent_error(
                    "Agent database missing after restart",
                    agent_id=agent_id,
                    state=record.state.value,
                    task=record.task,
                    db_path=str(db_path),
                )
                record.state_changed_at = time.time()
                await self.lifecycle.save(record)
                continue

            try:
                agent_fs = await Fsdantic.open(path=str(db_path))
            except Exception as exc:
                record.state = AgentState.ERRORED
                record.error = format_agent_error(
                    "Failed to open agent database",
                    agent_id=agent_id,
                    state=record.state.value,
                    task=record.task,
                    db_path=str(db_path),
                    error=str(exc),
                )
                record.state_changed_at = time.time()
                await self.lifecycle.save(record)
                continue

            await self.workspace_cache.put(str(db_path), agent_fs)

            ctx = AgentContext(
                agent_id=agent_id,
                task=record.task,
                priority=TaskPriority(record.priority),
                state=record.state,
                agent_db_path=db_path,
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

    async def shutdown(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task

        if self._running_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._running_tasks, return_exceptions=True),
                    timeout=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    "Some agent tasks did not complete before shutdown timeout",
                    extra={"active_count": len(self._running_tasks)},
                )

        await self.workspace_cache.clear()
        await self.workspace_manager.close_all()

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
        accept_stats = await self.accept_agent(command.agent_id)
        return CommandResult(
            command_type=command.type,
            agent_id=command.agent_id,
            payload=accept_stats,
        )

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
        agents_dict: dict[str, AgentSummary] = {
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

    async def _get_agent_workspace(self, ctx: AgentContext) -> Workspace:
        cache_key = str(ctx.agent_db_path)
        cached = await self.workspace_cache.get(cache_key)
        if cached is not None:
            ctx.agent_fs = cached
            return cached

        if ctx.agent_fs is not None:
            await self._close_agent_workspace(ctx)

        agent_fs = await Fsdantic.open(path=str(ctx.agent_db_path))
        ctx.agent_fs = agent_fs
        await self.workspace_cache.put(cache_key, agent_fs)
        return agent_fs

    async def _close_agent_workspace(self, ctx: AgentContext) -> None:
        if ctx.agent_fs is None:
            return
        try:
            await ctx.agent_fs.close()
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.warning("Failed to close agent workspace", exc_info=exc)
        ctx.agent_fs = None

    async def spawn_agent(self, task: str, priority: TaskPriority = TaskPriority.NORMAL) -> str:
        if self.lifecycle is None:
            raise RuntimeError("Orchestrator not initialized")

        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        agent_db = self.agentfs_dir / f"{agent_id}.db"
        agent_fs = await Fsdantic.open(path=str(agent_db))
        await self.workspace_cache.put(str(agent_db), agent_fs)

        ctx = AgentContext(
            agent_id=agent_id,
            task=task,
            priority=priority,
            state=AgentState.QUEUED,
            agent_db_path=agent_db,
            agent_fs=agent_fs,
        )
        self.active_agents[agent_id] = ctx

        try:
            await self._save_lifecycle_record(ctx)
            await self.queue.enqueue(agent_id, priority)
        except ResourceLimitError:
            self.active_agents.pop(agent_id, None)
            if self.lifecycle is not None:
                await self.lifecycle.delete(agent_id)
            await self.workspace_cache.remove(str(agent_db))
            raise

        await self.persist_state()
        return agent_id

    async def accept_agent(self, agent_id: str) -> dict[str, int]:
        """Accept an agent's overlay changes into stable.

        Returns merge statistics: ``files_merged`` (overlay files written to
        stable) and ``tombstones_applied`` (deletions recorded by the sandbox
        re-import that were applied to stable).
        """
        ctx = self._get_agent(agent_id)
        if ctx.state is not AgentState.REVIEWING:
            raise ValueError(f"Agent {agent_id} not in reviewing state")

        if self.stable is None:
            raise RuntimeError("Stable workspace not initialized")

        agent_fs = await self._get_agent_workspace(ctx)
        merge_result = await self.stable.overlay.merge(agent_fs, strategy=MergeStrategy.OVERWRITE)
        merge_errors = getattr(merge_result, "errors", None)
        if merge_errors:
            if isinstance(merge_errors, (list, tuple, set)):
                errors_list = list(merge_errors)
            else:
                errors_list = [str(merge_errors)]
            raise WorkspaceMergeError(
                format_agent_error(
                    "Failed to merge agent overlay",
                    agent_id=agent_id,
                    state=ctx.state.value,
                    conflicts=errors_list,
                ),
                error_code="WORKSPACE_MERGE_FAILED",
                context={
                    "agent_id": agent_id,
                    "conflicts": errors_list,
                    "conflict_count": len(errors_list),
                },
            )

        tombstones_applied = getattr(merge_result, "tombstones_applied", 0)
        files_merged = getattr(merge_result, "files_merged", 0)
        if tombstones_applied:
            logger.info(
                "Accept merge applied tombstones",
                extra={
                    "agent_id": agent_id,
                    "tombstones_applied": tombstones_applied,
                    "files_merged": files_merged,
                },
            )

        ctx.transition(AgentState.ACCEPTED)
        await self._save_lifecycle_record(ctx)
        await self.trash_agent(agent_id)
        return {"files_merged": files_merged, "tombstones_applied": tombstones_applied}

    async def reject_agent(self, agent_id: str) -> None:
        ctx = self._get_agent(agent_id)
        if ctx.state not in {AgentState.REVIEWING, AgentState.QUEUED}:
            raise ValueError(f"Agent {agent_id} not in reviewing state")

        ctx.transition(AgentState.REJECTED)
        await self._save_lifecycle_record(ctx)
        await self.trash_agent(agent_id)

    async def trash_agent(self, agent_id: str) -> None:
        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            return

        agent_db = ctx.agent_db_path
        bin_db = self.agentfs_dir / f"bin-{agent_id}.db"

        try:
            removed = await self.workspace_cache.remove(str(agent_db))
            if removed:
                ctx.agent_fs = None
            else:
                await self._close_agent_workspace(ctx)

            if agent_db.exists() and not bin_db.exists():
                shutil.move(agent_db, bin_db)
                ctx.agent_db_path = bin_db

            if self.lifecycle is not None:
                try:
                    await self.lifecycle.update_atomic(
                        ctx.agent_id,
                        lambda record: self._apply_lifecycle_update(record, ctx, bin_db),
                    )
                except VersionConflictError:
                    logger.warning(
                        "Failed to update lifecycle after version conflicts",
                        extra={"agent_id": ctx.agent_id},
                    )
                except LifecycleError:
                    record = LifecycleRecord(
                        agent_id=ctx.agent_id,
                        task=ctx.task,
                        priority=int(ctx.priority),
                        state=ctx.state,
                        created_at=ctx.created_at,
                        state_changed_at=ctx.state_changed_at,
                        updated_at=time.time(),
                        db_path=str(bin_db),
                        submission=ctx.submission,
                        error=ctx.error,
                    )
                    await self.lifecycle.save(record)

            workspace = self.cairn_home / "workspaces" / agent_id
            if workspace.exists():
                shutil.rmtree(workspace)
        finally:
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

            await self._execute_agent_lifecycle(ctx)
        except (ResourceLimitError, CairnTimeoutError, SandboxExecutionError) as exc:
            await self._handle_agent_error(ctx, exc)
            return
        except CairnError as exc:
            await self._handle_agent_error(ctx, exc)
            if isinstance(exc, RecoverableError):
                return
        except Exception as exc:
            await self._handle_agent_error(ctx, exc)
        finally:
            self._semaphore.release()
            await self.persist_state()

    async def _execute_agent_lifecycle(self, ctx: AgentContext) -> None:
        """Run the full agent lifecycle through each phase."""
        await self._transition_agent_state(ctx, AgentState.GENERATING)

        generated = await self._generate_code(ctx)
        if generated is None:
            return

        await self._transition_agent_state(ctx, AgentState.EXECUTING)

        result = await self._execute_code(ctx, generated)
        ctx.execution_result = {"status": "complete"}
        ctx.submission = result.submission

        await self._transition_agent_state(ctx, AgentState.SUBMITTING)
        await self._submit_results(ctx)
        await self._transition_agent_state(ctx, AgentState.REVIEWING)

    async def _transition_agent_state(self, ctx: AgentContext, new_state: AgentState) -> None:
        """Persist an agent state transition."""
        ctx.transition(new_state)
        await self._save_lifecycle_record(ctx)
        await self.persist_state()

    async def _generate_code(self, ctx: AgentContext) -> str | None:
        """Fetch and validate provider code for the agent."""
        if self.stable is None:
            raise RuntimeError("Stable workspace not initialized")

        agent_fs = await self._get_agent_workspace(ctx)
        context = {"agent_id": ctx.agent_id, "workspace": agent_fs, "stable": self.stable}

        try:
            generated = await self.code_provider.get_code(ctx.task, context)
        except ProviderError as exc:
            ctx.error = str(exc)
            await self._transition_agent_state(ctx, AgentState.ERRORED)
            return None

        ctx.generated_code = generated
        is_valid, error = await self.code_provider.validate_code(generated)
        if not is_valid:
            ctx.error = error or "Code provider validation failed"
            await self._transition_agent_state(ctx, AgentState.ERRORED)
            return None

        return generated

    async def _execute_code(self, ctx: AgentContext, generated: str) -> SandboxResult:
        """Execute generated code in the bwrap sandbox over the materialized workspace.

        The executor materializes the agent overlay (over stable) to a real
        directory, runs the code in the sandbox, and re-imports the changeset
        back into the agent overlay.
        """
        if self.stable is None:
            raise RuntimeError("Stable workspace not initialized")

        agent_fs = ctx.agent_fs
        if agent_fs is None:
            agent_fs = await self._get_agent_workspace(ctx)

        workdir = self.cairn_home / "workspaces" / ctx.agent_id
        executor = self.executor_factory(
            agent_id=ctx.agent_id,
            workdir=workdir,
            agent_fs=agent_fs,
            stable=self.stable,
            settings=self.executor_settings,
            allow_root=self.cairn_home / "workspaces",
        )
        return await executor.run(code=generated, task=ctx.task)

    async def _submit_results(self, ctx: AgentContext) -> None:
        """Persist the agent submission to the workspace KV store.

        The submission payload itself is produced by the sandbox (written to
        ``.cairn/submission.json``) and read back during execution; this step
        only persists it to the canonical KV location consumed by lifecycle.
        """
        if ctx.submission is None:
            return

        agent_fs = ctx.agent_fs
        if agent_fs is None:
            agent_fs = await self._get_agent_workspace(ctx)

        submission_record = SubmissionRecord(
            agent_id=ctx.agent_id,
            submission=ctx.submission,
            updated_at=time.time(),
        )
        submission_repo = agent_fs.kv.repository(prefix="", model_type=SubmissionRecord)
        await submission_repo.save(SUBMISSION_KEY, submission_record)

    async def _handle_agent_error(self, ctx: AgentContext | None, exc: Exception) -> None:
        """Record agent failure details and persist lifecycle state."""
        if ctx is None:
            return

        ctx.error = str(exc)
        ctx.transition(AgentState.ERRORED)
        await self._save_lifecycle_record(ctx)

        # Remove the sandbox workdir so failed runs leave no review surface.
        workdir = self.cairn_home / "workspaces" / ctx.agent_id
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)

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

    async def cleanup_completed_agents(
        self,
        max_age_seconds: float = LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
    ) -> int:
        if self.lifecycle is None:
            return 0
        return await self.lifecycle.cleanup_old(max_age_seconds, self.agentfs_dir)

    def _apply_lifecycle_update(
        self,
        record: LifecycleRecord,
        ctx: AgentContext,
        db_path: Path,
    ) -> None:
        record.task = ctx.task
        record.priority = int(ctx.priority)
        record.state = ctx.state
        record.state_changed_at = ctx.state_changed_at
        record.db_path = str(db_path)
        record.submission = ctx.submission
        record.error = ctx.error

    async def _save_lifecycle_record(self, ctx: AgentContext) -> None:
        lifecycle = self.lifecycle
        if lifecycle is None:
            return

        db_path = ctx.agent_db_path
        if not db_path.exists():
            bin_path = self.agentfs_dir / f"bin-{ctx.agent_id}.db"
            if bin_path.exists():
                db_path = bin_path

        existing = await lifecycle.load(ctx.agent_id)
        if existing:
            try:
                await lifecycle.update_atomic(
                    ctx.agent_id,
                    lambda record: self._apply_lifecycle_update(record, ctx, db_path),
                )
            except VersionConflictError:
                logger.warning(
                    "Persistent version conflict saving lifecycle",
                    extra={"agent_id": ctx.agent_id, "state": ctx.state.value},
                )
            return

        record = LifecycleRecord(
            agent_id=ctx.agent_id,
            task=ctx.task,
            priority=int(ctx.priority),
            state=ctx.state,
            created_at=ctx.created_at,
            state_changed_at=ctx.state_changed_at,
            updated_at=time.time(),
            db_path=str(db_path),
            submission=ctx.submission,
            error=ctx.error,
        )

        @with_retry(
            max_attempts=LIFECYCLE_MAX_RETRY_ATTEMPTS,
            initial_delay=LIFECYCLE_RETRY_INITIAL_DELAY_SECONDS,
            max_delay=LIFECYCLE_RETRY_INITIAL_DELAY_SECONDS,
            backoff_factor=LIFECYCLE_RETRY_BACKOFF_FACTOR,
            retry_exceptions=(RecoverableError,),
        )
        async def _persist_record() -> None:
            await lifecycle.save(record)

        await _persist_record()

    def _get_agent(self, agent_id: str) -> AgentContext:
        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            raise KeyError(f"Unknown agent_id: {agent_id}")
        return ctx
