"""Core Cairn orchestrator for agent lifecycle management."""

from __future__ import annotations

import asyncio
import hashlib
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
    UndoCommand,
)
from cairn.core.constants import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
    LIFECYCLE_MAX_RETRY_ATTEMPTS,
    LIFECYCLE_RETRY_BACKOFF_FACTOR,
    LIFECYCLE_RETRY_INITIAL_DELAY_SECONDS,
    MAX_STORED_LOG_BYTES,
    STATE_PERSIST_MIN_INTERVAL_SECONDS,
    WORKER_ERROR_BACKOFF_SECONDS,
)
from cairn.core.exceptions import (
    AgentNotFoundError,
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
from cairn.orchestrator.lifecycle import (
    LIFECYCLE_MIRROR_NAME,
    RUN_KEY,
    SUBMISSION_KEY,
    LifecycleRecord,
    LifecycleStore,
    RunRecord,
    SubmissionRecord,
    UndoRecord,
)
from cairn.orchestrator.queue import TaskPriority, TaskQueue
from cairn.orchestrator.signals import SignalHandler
from cairn.providers.providers import CodeProvider, FileCodeProvider
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.sandbox import (
    SANDBOX_DIR_NAME,
    BwrapExecutor,
    SandboxExecutionError,
    SandboxExecutor,
    SandboxResult,
)
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.runtime.workspace_cache import WorkspaceCache
from cairn.runtime.workspace_manager import WorkspaceManager
from cairn.utils.error_formatting import format_agent_error
from cairn.utils.retry import with_retry
from cairn.watcher.watcher import FileWatcher

logger = logging.getLogger(__name__)

TERMINAL_STATES = {
    AgentState.REVIEWING,
    AgentState.ACCEPTED,
    AgentState.REJECTED,
    AgentState.ERRORED,
}

INTERRUPTED_STATES = {AgentState.GENERATING, AgentState.EXECUTING, AgentState.SUBMITTING}


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
        self._last_persist = 0.0
        self.workspace_manager = WorkspaceManager()
        self.workspace_cache = WorkspaceCache(max_size=self.config.workspace_cache_size)

    async def initialize(self) -> None:
        self.agentfs_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("workspaces", "signals", "state"):
            (self.cairn_home / directory).mkdir(parents=True, exist_ok=True)

        self.stable = await self.workspace_manager.create_workspace(
            self.agentfs_dir / "stable.db",
            max_content_bytes=self.config.max_content_bytes,
        )
        self.bin = await self.workspace_manager.create_workspace(
            self.agentfs_dir / "bin.db",
            max_content_bytes=self.config.max_content_bytes,
        )

        self.watcher = FileWatcher(
            self.project_root,
            self.stable,
            max_file_bytes=self.config.max_sync_file_bytes,
            extra_ignore_dirs=self.config.extra_ignore_dirs,
        )
        self.signals = SignalHandler(self.cairn_home, self, enable_polling=self.config.enable_signal_polling)
        self.lifecycle = LifecycleStore(self.bin)

        if self.config.sync_project_on_start:
            await self.watcher.initial_sync()

        await self.recover_from_lifecycle_store()
        await self._mirror_lifecycle()
        if self.config.start_worker_on_init:
            self._ensure_worker()
        await self.persist_state()

    async def _mirror_lifecycle(self) -> None:
        """Write the lifecycle mirror that CLI read-only queries consume.

        pyturso locks ``bin.db`` exclusively even for read-only opens, so the
        CLI cannot open it while the daemon runs; the mirror under
        ``$CAIRN_HOME/state/lifecycle.json`` is the CLI's query path.  Agents
        whose self-report diverges from what they actually did also carry the
        ground-truth path lists in the mirror so `cairn status` can show them.
        """
        if self.lifecycle is None:
            return
        try:
            records = await self.lifecycle.list_all()
        except Exception:
            logger.exception("Failed to list lifecycle records for mirror")
            return
        payload: dict[str, dict] = {}
        for record in records:
            data = record.model_dump(mode="json")
            if record.claim_mismatch:
                run = await self._load_run_record_for(record)
                if run is not None:
                    data["run_written"] = run.written
                    data["run_deleted"] = run.deleted
            if record.state is AgentState.ERRORED:
                # Keep failed-run logs readable via `cairn logs` (P4.3); the
                # mirror is the only lock-free path into a run record while the
                # daemon owns the databases.
                run = await self._load_run_record_for(record)
                if run is not None and run.log:
                    data["run_log"] = run.log
            payload[record.agent_id] = data
        await asyncio.to_thread(self._write_mirror_sync, payload)

    async def _load_run_record_for(self, record: LifecycleRecord) -> RunRecord | None:
        """Load an agent's run record from its (possibly trashed) workspace db."""
        try:
            db_path = Path(record.db_path)
            if not db_path.exists():
                return None
            agent_fs = await Fsdantic.open(path=str(db_path))
        except Exception:  # noqa: BLE001 - best-effort mirror enrichment
            return None
        try:
            repo = agent_fs.kv.repository(prefix="", model_type=RunRecord)
            return await repo.load(RUN_KEY)
        except Exception:  # noqa: BLE001 - best-effort mirror enrichment
            return None
        finally:
            await agent_fs.close()

    def _write_mirror_sync(self, payload: dict) -> None:
        state_dir = self.cairn_home / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = state_dir / f"{LIFECYCLE_MIRROR_NAME}.tmp"
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(state_dir / LIFECYCLE_MIRROR_NAME)

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
            except Exception as exc:  # noqa: BLE001 - any open failure -> explicit ERRORED
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
            elif ctx.state in INTERRUPTED_STATES:
                # The process died mid-run.  The sandbox workdir and any partial
                # re-import cannot be trusted, so fail the agent explicitly
                # rather than leaving a record nothing can ever resolve.
                ctx.error = format_agent_error(
                    "Interrupted by orchestrator restart",
                    agent_id=agent_id,
                    state=ctx.state.value,
                    task=record.task,
                )
                ctx.transition(AgentState.ERRORED)
                await self._save_lifecycle_record(ctx)
                if self.config.requeue_interrupted:
                    await self.spawn_agent(record.task, TaskPriority(record.priority))

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
            case UndoCommand():
                return await self._handle_undo(command)
        raise ValueError(f"Unsupported command type: {command.type.value}")

    async def wait_for_agent(
        self,
        agent_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 0.05,
    ) -> LifecycleRecord:
        """Block until the agent reaches a terminal state."""
        if self.lifecycle is None:
            raise RuntimeError("Orchestrator not initialized")
        deadline = time.monotonic() + timeout
        while True:
            record = await self.lifecycle.load(agent_id)
            if record is not None and record.state in TERMINAL_STATES:
                return record
            if time.monotonic() >= deadline:
                raise CairnTimeoutError(
                    f"Agent {agent_id} did not settle within {timeout}s",
                    error_code="AGENT_WAIT_TIMEOUT",
                    context={"agent_id": agent_id, "timeout_seconds": timeout},
                )
            await asyncio.sleep(poll_interval)

    async def _handle_queue(self, command: QueueCommand) -> CommandResult:
        agent_id = await self.spawn_agent(task=command.task, priority=command.priority)
        return CommandResult(command_type=command.type, agent_id=agent_id)

    async def _handle_accept(self, command: AcceptCommand) -> CommandResult:
        accept_stats = await self.accept_agent(command.agent_id, force=command.force)
        return CommandResult(
            command_type=command.type,
            agent_id=command.agent_id,
            payload=accept_stats,
        )

    async def _handle_undo(self, command: UndoCommand) -> CommandResult:
        stats = await self.undo_accept(command.agent_id)
        return CommandResult(command_type=command.type, agent_id=command.agent_id, payload=stats)

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
            raise AgentNotFoundError(
                f"Unknown agent_id: {command.agent_id}",
                error_code="AGENT_NOT_FOUND",
                context={"agent_id": command.agent_id},
            )

        record = await self.lifecycle.load(command.agent_id)
        if record is None:
            raise AgentNotFoundError(
                f"Unknown agent_id: {command.agent_id}",
                error_code="AGENT_NOT_FOUND",
                context={"agent_id": command.agent_id},
            )

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
        except BaseException:
            self.active_agents.pop(agent_id, None)
            await self.workspace_cache.remove(str(agent_db))
            if self.lifecycle is not None:
                with suppress(Exception):
                    await self.lifecycle.delete(agent_id)
            with suppress(OSError):
                agent_db.unlink(missing_ok=True)
            raise

        await self.persist_state()
        return agent_id

    async def accept_agent(self, agent_id: str, *, force: bool = False) -> dict[str, int]:
        """Accept an agent's overlay changes into stable.

        Unless ``force``, refuses when stable changed for paths the agent
        touched after the agent read them (the accept would silently discard
        those edits).  Returns merge statistics: ``files_merged`` and
        ``tombstones_applied``.
        """
        ctx = self._get_agent(agent_id)
        if ctx.state is not AgentState.REVIEWING:
            raise ValueError(f"Agent {agent_id} not in reviewing state")

        if self.stable is None:
            raise RuntimeError("Stable workspace not initialized")

        agent_fs = await self._get_agent_workspace(ctx)
        run = await self._load_run_record(agent_fs)

        if run is not None and not force:
            stale = await self._detect_stale_paths(run)
            if stale:
                raise WorkspaceMergeError(
                    format_agent_error(
                        "Stable changed since this agent started; accepting would discard those changes",
                        agent_id=agent_id,
                        state=ctx.state.value,
                        stale_paths=stale,
                    ),
                    error_code="ACCEPT_STALE_BASE",
                    context={"agent_id": agent_id, "stale_paths": stale},
                )

        await self._snapshot_for_undo(ctx, run)
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
        stats = {"files_merged": files_merged, "tombstones_applied": tombstones_applied}
        await self._record_accept_stats(agent_id, stats)
        return stats

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def _load_run_record(self, agent_fs: Workspace) -> RunRecord | None:
        """Load the agent's run record from its workspace, if present."""
        try:
            repo = agent_fs.kv.repository(prefix="", model_type=RunRecord)
            return await repo.load(RUN_KEY)
        except Exception:  # noqa: BLE001 - run record may not exist yet
            return None

    async def _detect_stale_paths(self, run: RunRecord) -> list[str]:
        """Paths whose content in stable changed after the agent read them.

        base_hashes holds the digest each touched path had in the materialized
        workspace at run start.  If stable's current content no longer matches,
        something (you, the watcher, another agent) changed it in the meantime
        and an OVERWRITE merge would silently discard that change.
        """
        if self.stable is None:
            return []
        stale: list[str] = []
        for rel, base_digest in run.base_hashes.items():
            try:
                current = await self.stable.files.read(rel, mode="binary")
            except Exception:  # noqa: BLE001, S112 - absent now: deletion is handled by tombstones
                continue
            if self._sha256_bytes(current) != base_digest:
                stale.append(rel)
        return sorted(stale)

    async def reject_agent(self, agent_id: str) -> None:
        ctx = self._get_agent(agent_id)
        if ctx.state not in {AgentState.REVIEWING, AgentState.QUEUED, AgentState.ERRORED}:
            raise ValueError(
                f"Agent {agent_id} cannot be rejected from state {ctx.state.value} "
                f"(allowed: reviewing, queued, errored)"
            )

        # Drop any still-queued entry so the worker never dequeues a phantom.
        await self.queue.remove(agent_id)

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
                        files_written=ctx.files_written,
                        files_deleted=ctx.files_deleted,
                        claim_mismatch=ctx.claim_mismatch,
                    )
                    await self.lifecycle.save(record)

            workspace = self.cairn_home / "workspaces" / agent_id
            if workspace.exists():
                shutil.rmtree(workspace)
        finally:
            self.active_agents.pop(agent_id, None)
            await self.persist_state()
        await self._mirror_lifecycle()

    async def _worker_loop(self) -> None:
        while True:
            try:
                queued = await self.queue.dequeue_wait()
                agent_id = queued.task
                await self._semaphore.acquire()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop iteration failed; continuing")
                await asyncio.sleep(WORKER_ERROR_BACKOFF_SECONDS)
                continue

            task = asyncio.create_task(self._run_agent(agent_id))
            self._running_tasks.add(task)
            task.add_done_callback(self._on_agent_task_done)

    def _on_agent_task_done(self, task: asyncio.Task[None]) -> None:
        self._running_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Agent task raised out of _run_agent", exc_info=exc)

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            self._worker_task.add_done_callback(self._on_worker_done)

    def _on_worker_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        logger.error("Worker loop exited unexpectedly; restarting", exc_info=task.exception())
        self._ensure_worker()

    async def _run_agent(self, agent_id: str) -> None:
        ctx = self.active_agents.get(agent_id)

        try:
            if ctx is None:
                return

            async with self.workspace_cache.pinned(str(ctx.agent_db_path)):
                await self._execute_agent_lifecycle(ctx)
        except (ResourceLimitError, CairnTimeoutError, SandboxExecutionError) as exc:
            await self._handle_agent_error(ctx, exc)
            return
        except Exception as exc:  # noqa: BLE001 - any failure is recorded as an agent error
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
        ctx.submission = result.submission
        await self._record_run(ctx, result)

        await self._transition_agent_state(ctx, AgentState.SUBMITTING)
        await self._submit_results(ctx)
        await self._transition_agent_state(ctx, AgentState.REVIEWING)

    async def _record_run(self, ctx: AgentContext, result: SandboxResult) -> None:
        """Persist the sandbox's ground-truth changeset for human review."""
        agent_fs = ctx.agent_fs or await self._get_agent_workspace(ctx)
        record = RunRecord(
            agent_id=ctx.agent_id,
            written=result.changes["written"],
            deleted=result.changes["deleted"],
            base_hashes=result.base_hashes,
            log=result.log[-MAX_STORED_LOG_BYTES:],
            exit_code=result.exit_code,
            executable=result.executable,
            directories=result.directories,
            updated_at=time.time(),
        )
        repo = agent_fs.kv.repository(prefix="", model_type=RunRecord)
        await repo.save(RUN_KEY, record)

        ctx.files_written = len(record.written)
        ctx.files_deleted = len(record.deleted)
        claimed = set((result.submission or {}).get("changed_files", []))
        actual = set(record.written) | set(record.deleted)
        ctx.claim_mismatch = bool(claimed) and claimed != actual

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

        # Keep the workdir: run.log and the partial changeset are the only
        # record of what the agent did before it failed.  Cleanup happens on
        # the normal retention schedule or immediately via `cairn reject`.
        workdir = self.cairn_home / "workspaces" / ctx.agent_id
        log_path = workdir / SANDBOX_DIR_NAME / "run.log"
        if log_path.exists():
            with suppress(Exception):
                await self._record_partial_run(ctx, log_path.read_text(encoding="utf-8"))

        await self._save_lifecycle_record(ctx)

    async def _record_partial_run(self, ctx: AgentContext, log: str) -> None:
        """Persist what the sandbox produced before it failed."""
        agent_fs = ctx.agent_fs or await self._get_agent_workspace(ctx)
        record = RunRecord(
            agent_id=ctx.agent_id,
            log=log[-MAX_STORED_LOG_BYTES:],
            exit_code=1,
            updated_at=time.time(),
        )
        repo = agent_fs.kv.repository(prefix="", model_type=RunRecord)
        await repo.save(RUN_KEY, record)

    async def persist_state(self) -> None:
        now = time.monotonic()
        if now - self._last_persist < STATE_PERSIST_MIN_INTERVAL_SECONDS:
            return
        self._last_persist = now
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
        await asyncio.to_thread(self._write_state_atomic, payload)

    def _write_state_atomic(self, payload: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_file)

    async def cleanup_completed_agents(
        self,
        max_age_seconds: float = LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
    ) -> int:
        if self.lifecycle is None:
            return 0
        cleaned = await self.lifecycle.cleanup_old(
            max_age_seconds,
            self.agentfs_dir,
            cache=self.workspace_cache,
            cairn_home=self.cairn_home,
        )
        await self._mirror_lifecycle()
        return cleaned

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
        record.files_written = ctx.files_written
        record.files_deleted = ctx.files_deleted
        record.claim_mismatch = ctx.claim_mismatch

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
            await self._mirror_lifecycle()
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
            files_written=ctx.files_written,
            files_deleted=ctx.files_deleted,
            claim_mismatch=ctx.claim_mismatch,
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
        await self._mirror_lifecycle()

    def _get_agent(self, agent_id: str) -> AgentContext:
        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            raise AgentNotFoundError(
                f"Unknown agent_id: {agent_id}",
                error_code="AGENT_NOT_FOUND",
                context={"agent_id": agent_id},
            )
        return ctx

    async def _record_accept_stats(self, agent_id: str, stats: dict[str, int]) -> None:
        """Persist accept merge statistics on the lifecycle record."""
        if self.lifecycle is None:
            return
        try:
            await self.lifecycle.update_atomic(
                agent_id,
                lambda record: setattr(record, "accept_stats", stats),
            )
        except LifecycleError:
            logger.debug("Could not persist accept stats", extra={"agent_id": agent_id})
        await self._mirror_lifecycle()

    async def _snapshot_for_undo(self, ctx: AgentContext, run: RunRecord | None) -> None:
        """Save stable's pre-merge content for the paths this accept will touch."""
        if run is None or self.bin is None or self.stable is None:
            return
        prefix = f"undo/{ctx.agent_id}/"
        restored: list[str] = []
        removed: list[str] = []
        for rel in sorted(set(run.written) | set(run.deleted)):
            try:
                content = await self.stable.files.read(rel, mode="binary")
            except Exception:  # noqa: BLE001 - did not exist before: undo = delete
                removed.append(rel)  # did not exist before: undo = delete
                continue
            await self.bin.files.write(prefix + rel, content, mode="binary")
            restored.append(rel)

        repo = self.bin.kv.repository(prefix="", model_type=UndoRecord)
        await repo.save(
            f"undo:{ctx.agent_id}",
            UndoRecord(
                agent_id=ctx.agent_id,
                restore_paths=restored,
                delete_paths=removed,
                created_at=time.time(),
                updated_at=time.time(),
            ),
        )

    async def undo_accept(self, agent_id: str) -> dict[str, int]:
        """Restore stable to its pre-accept state for one agent's changes."""
        if self.bin is None or self.stable is None:
            raise RuntimeError("Bin/stable workspace not initialized")
        repo = self.bin.kv.repository(prefix="", model_type=UndoRecord)
        undo = await repo.load(f"undo:{agent_id}")
        if undo is None:
            raise AgentNotFoundError(
                f"No undo record for {agent_id} (already expired or never accepted)",
                error_code="UNDO_NOT_FOUND",
            )
        prefix = f"undo/{agent_id}/"
        for rel in undo.restore_paths:
            content = await self.bin.files.read(prefix + rel, mode="binary")
            await self.stable.files.write(rel, content, mode="binary")
        for rel in undo.delete_paths:
            with suppress(Exception):
                await self.stable.files.remove(rel)
        await repo.delete(f"undo:{agent_id}")
        return {"restored": len(undo.restore_paths), "deleted": len(undo.delete_paths)}
