"""Core Cairn orchestrator for agent lifecycle management."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from fsdantic import Fsdantic, Workspace

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
    ACCEPTING_KEY_PREFIX,
    LIFECYCLE_MIRROR_NAME,
    RUN_KEY,
    SUBMISSION_KEY,
    AcceptingRecord,
    LifecycleRecord,
    LifecycleStore,
    RunRecord,
    SubmissionRecord,
    UndoRecord,
)
from cairn.orchestrator.queue import TaskPriority, TaskQueue
from cairn.orchestrator.transport import CommandTable, OrchestratorTransport, socket_path
from cairn.providers.providers import CodeProvider, FileCodeProvider
from cairn.runtime import repo
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.integration import IntegrationLock
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

logger = logging.getLogger(__name__)

TERMINAL_STATES = {
    AgentState.REVIEWING,
    AgentState.ACCEPTED,
    AgentState.REJECTED,
    AgentState.ERRORED,
}

INTERRUPTED_STATES = {AgentState.GENERATING, AgentState.EXECUTING, AgentState.SUBMITTING}


def _entry_changed(base: repo.ManifestEntry, current: repo.ManifestEntry) -> bool:
    """Full-fidelity comparison of a base entry vs the current tree entry:
    kind, content (digest) or symlink target, and permission bits."""
    if base.kind != current.kind:
        return True
    if base.kind == "symlink":
        return base.link_target != current.link_target
    if base.kind == "file":
        return base.digest != current.digest or base.mode != current.mode
    return False


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

        self.bin: Workspace | None = None
        self.active_agents: dict[str, AgentContext] = {}
        self.queue = TaskQueue(max_size=self.config.max_queue_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_agents)
        self._running_tasks: set[asyncio.Task[None]] = set()

        self.code_provider = code_provider or FileCodeProvider(base_path=self.project_root)
        self.executor_factory = executor_factory or BwrapExecutor

        self.transport: OrchestratorTransport | None = None
        self.command_table: CommandTable | None = None
        self.lifecycle: LifecycleStore | None = None
        self.state_file = self.cairn_home / "state" / "orchestrator.json"
        self._last_persist = 0.0
        self.workspace_manager = WorkspaceManager()
        self.workspace_cache = WorkspaceCache(max_size=self.config.workspace_cache_size)

    async def initialize(self) -> None:
        self.agentfs_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("workspaces", "state"):
            (self.cairn_home / directory).mkdir(parents=True, exist_ok=True)

        self.bin = await self.workspace_manager.create_workspace(
            self.agentfs_dir / "bin.db",
            max_content_bytes=self.config.max_content_bytes,
        )

        self.lifecycle = LifecycleStore(self.bin)
        self.command_table = CommandTable(self.bin)
        await self.command_table.recover_inflight()
        self.transport = OrchestratorTransport(
            socket_path(self.cairn_home),
            self.submit_command,
            self.command_table,
        )
        await self.transport.start()

        await self.recover_from_lifecycle_store()
        await self._recover_interrupted_accepts()
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
        """Atomically rewrite the lifecycle mirror.

        Uses a unique temporary file + ``fsync`` + ``os.replace`` so
        concurrent mirror producers (parallel agent transitions) can never
        truncate/write/rename the same temp path (review §3.8).
        """
        state_dir = self.cairn_home / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=state_dir, prefix=f"{LIFECYCLE_MIRROR_NAME}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, state_dir / LIFECYCLE_MIRROR_NAME)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp_name)
            raise

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
        assert self.transport is not None
        await self.transport.serve_forever()

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

        if self.transport is not None:
            await self.transport.close()
            self.transport = None
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
        """Apply an agent's computed changeset to the actual working tree.

        The real Git working tree is the canonical source of truth (review
        §4.2).  The whole mutation runs under one project integration lock:
        revalidate the base every touched path had at run start (unless
        ``force``; any discrepancy — including a missing run record — fails
        the gate closed with ``ACCEPT_STALE_BASE``), write a durable
        ``ACCEPTING`` journal entry, snapshot pre-apply content for undo, and
        apply.  A crash mid-apply is rolled back on the next startup from the
        snapshot.  Returns ``{"files_written": n, "files_deleted": n}``.
        """
        ctx = self._get_agent(agent_id)
        if ctx.state is not AgentState.REVIEWING:
            raise ValueError(f"Agent {agent_id} not in reviewing state")

        agent_fs = await self._get_agent_workspace(ctx)
        run = await self._load_run_record(agent_fs)

        if run is None:
            # Fail closed: without the run record there is no ground truth
            # for what the agent touched, so the base cannot be revalidated.
            raise WorkspaceMergeError(
                format_agent_error(
                    "Run record missing; cannot revalidate the base — refusing to apply",
                    agent_id=agent_id,
                    state=ctx.state.value,
                    stale_paths=[],
                ),
                error_code="ACCEPT_STALE_BASE",
                context={"agent_id": agent_id, "reason": "missing run record"},
            )

        async with IntegrationLock(self.agentfs_dir / "integration.lock"):
            if not force:
                stale = await self._detect_stale_paths(run)
                if stale:
                    raise WorkspaceMergeError(
                        format_agent_error(
                            "The working tree changed since this agent started; accepting would discard those changes",
                            agent_id=agent_id,
                            state=ctx.state.value,
                            stale_paths=stale,
                        ),
                        error_code="ACCEPT_STALE_BASE",
                        context={"agent_id": agent_id, "stale_paths": stale},
                    )

            await self._begin_accept(ctx)
            try:
                await self._snapshot_for_undo(ctx, run)
                stats, applied_digests = await self._apply_to_tree(ctx, run)
            except BaseException:
                await self._abort_accept(ctx)
                raise
            await self._record_applied_state(ctx, applied_digests)
            await self._commit_accept(ctx)

        ctx.transition(AgentState.ACCEPTED)
        await self._save_lifecycle_record(ctx)
        await self.trash_agent(agent_id)
        await self._record_accept_stats(agent_id, stats)
        return stats

    # ------------------------------------------------------------------
    # Durable accept transaction journal (review §3.2)
    # ------------------------------------------------------------------

    async def _begin_accept(self, ctx: AgentContext) -> None:
        if self.bin is None:
            return
        journal = self.bin.kv.repository(prefix=ACCEPTING_KEY_PREFIX, model_type=AcceptingRecord)
        await journal.save(
            ctx.agent_id,
            AcceptingRecord(agent_id=ctx.agent_id, started_at=time.time(), updated_at=time.time()),
        )

    async def _abort_accept(self, ctx: AgentContext) -> None:
        if self.bin is None:
            return
        journal = self.bin.kv.repository(prefix=ACCEPTING_KEY_PREFIX, model_type=AcceptingRecord)
        await journal.delete(ctx.agent_id)

    async def _commit_accept(self, ctx: AgentContext) -> None:
        if self.bin is None:
            return
        journal = self.bin.kv.repository(prefix=ACCEPTING_KEY_PREFIX, model_type=AcceptingRecord)
        await journal.delete(ctx.agent_id)

    async def _record_applied_state(self, ctx: AgentContext, applied_digests: dict[str, str]) -> None:
        """Store the post-apply digests on the undo record so `cairn undo` can
        validate that the accepted state is still present before reverting."""
        if self.bin is None:
            return
        repo = self.bin.kv.repository(prefix="", model_type=UndoRecord)
        undo = await repo.load(f"undo:{ctx.agent_id}")
        if undo is None:
            return
        undo.applied_digests = applied_digests
        await repo.save(f"undo:{ctx.agent_id}", undo)

    async def _recover_interrupted_accepts(self) -> None:
        """Roll back any accept that died mid-apply (durable journal recovery).

        The tree is restored to its pre-apply state via the undo snapshot and
        the agent is failed explicitly; the journal entry is consumed.  This
        is fail-safe: the snapshot was written before any tree mutation, so
        a crash before the snapshot leaves nothing to roll back.
        """
        if self.bin is None or self.lifecycle is None:
            return
        journal = self.bin.kv.repository(prefix=ACCEPTING_KEY_PREFIX, model_type=AcceptingRecord)
        for record in await journal.list_all():
            agent_id = record.agent_id
            logger.warning(
                "Recovering interrupted accept (journal rollback)",
                extra={"agent_id": agent_id},
            )
            try:
                await self._rollback_accept(agent_id)
            except Exception as exc:  # noqa: BLE001 - keep going; surface via the record
                logger.error(
                    "Failed to roll back interrupted accept",
                    extra={"agent_id": agent_id, "error": str(exc)},
                )
            with suppress(Exception):
                await journal.delete(agent_id)

            lifecycle = await self.lifecycle.load(agent_id)
            if lifecycle is not None and lifecycle.state is AgentState.REVIEWING:
                lifecycle.error = format_agent_error(
                    "Accept interrupted by restart; working tree rolled back",
                    agent_id=agent_id,
                    state=AgentState.ERRORED.value,
                )
                lifecycle.state = AgentState.ERRORED
                lifecycle.state_changed_at = time.time()
                with suppress(Exception):
                    await self.lifecycle.save(lifecycle)

    async def _rollback_accept(self, agent_id: str) -> None:
        """Restore the tree from the undo snapshot; consume the undo record."""
        if self.bin is None:
            return
        repo = self.bin.kv.repository(prefix="", model_type=UndoRecord)
        undo = await repo.load(f"undo:{agent_id}")
        if undo is None:
            return  # nothing was snapshotted (crash before any mutation)
        prefix = f"undo/{agent_id}/"
        restore_contents: list[tuple[str, bytes]] = []
        for rel in undo.restore_paths:
            content = await self.bin.files.read(prefix + rel, mode="binary")
            if not isinstance(content, bytes):
                content = content.encode("utf-8")
            restore_contents.append((rel, content))
        await asyncio.to_thread(self._rollback_to_tree_sync, restore_contents, undo.delete_paths)
        with suppress(Exception):
            await repo.delete(f"undo:{agent_id}")

    def _rollback_to_tree_sync(self, restore_contents: list[tuple[str, bytes]], delete_paths: list[str]) -> None:
        """Blocking tree restore used by journal rollback recovery."""
        for rel, content in restore_contents:
            target = self.project_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content)
        for rel in delete_paths:
            target = self.project_root / rel
            with suppress(OSError):
                if target.is_symlink() or target.is_file():
                    target.unlink()

    async def _load_run_record(self, agent_fs: Workspace) -> RunRecord | None:
        """Load the agent's run record from its workspace, if present."""
        try:
            repo = agent_fs.kv.repository(prefix="", model_type=RunRecord)
            return await repo.load(RUN_KEY)
        except Exception:  # noqa: BLE001 - run record may not exist yet
            return None

    async def _detect_stale_paths(self, run: RunRecord) -> list[str]:
        """Touched paths whose current state in the real working tree differs
        from the base the agent saw at run start.

        The run record carries a full-fidelity base entry (kind/digest/mode/
        symlink target) for every touched path; any touched path absent from
        ``base_manifest`` was *explicitly absent* then.  Revalidating against a
        fresh manifest of the canonical tree catches delete/write collisions
        (a base entry that vanished), create/create collisions (an
        absent-at-start path that a human created meanwhile), content, mode,
        type, and symlink-target changes — anything an OVERWRITE-style apply
        would silently discard (review §2.6).
        """
        current = await asyncio.to_thread(repo.capture_manifest, self.project_root)
        stale: list[str] = []
        touched = set(run.written) | set(run.deleted) | set(run.mode_changed)

        if not run.base_manifest:
            # Legacy (pre-M4) records carry hash-only base data.
            for rel, base_digest in run.base_hashes.items():
                entry = current.entry_for(rel)
                if entry is None or entry.kind != "file" or entry.digest != base_digest:
                    stale.append(rel)
            for rel in touched:
                if rel not in run.base_hashes and current.entry_for(rel) is not None:
                    stale.append(rel)  # create/create collision
            return sorted(set(stale))

        for rel in touched:
            base_entry = run.base_manifest.get(rel)
            current_entry = current.entry_for(rel)
            if base_entry is not None:
                if current_entry is None:
                    stale.append(rel)  # delete/write collision: base entry vanished
                elif _entry_changed(base_entry, current_entry):
                    stale.append(rel)
            elif current_entry is not None:
                stale.append(rel)  # create/create collision

        return sorted(set(stale))

    def _validate_rel(self, rel: str) -> None:
        """Reject any path that could escape the project root or scaffolding."""
        if rel == "" or rel.startswith("/") or ".." in rel.split("/"):
            raise WorkspaceMergeError(
                f"Invalid path in changeset: {rel!r}",
                error_code="WORKSPACE_MERGE_FAILED",
                context={"path": rel},
            )

    async def _apply_to_tree(self, ctx: AgentContext, run: RunRecord) -> tuple[dict[str, int], dict[str, str]]:
        """Apply the agent's computed changeset from its disposable workspace
        to the actual working tree.

        The changeset (``written``/``deleted``/``executable``/``directories``/
        ``mode_changed``) was computed by the executor from the workspace
        diff — never trusted from the agent's submission prose.  The apply is
        strict: any failure raises (the durable ``ACCEPTING`` journal lets
        recovery roll the tree back), never a silent partial success (review
        §2.7b).  Returns ``(stats, applied_digests)`` where
        ``applied_digests`` maps written paths to their post-apply content
        digests (used by undo to validate the accepted state).
        """
        workdir = self.cairn_home / "workspaces" / ctx.agent_id
        if not workdir.is_dir():
            raise WorkspaceMergeError(
                f"Disposable workspace missing for {ctx.agent_id}",
                error_code="WORKSPACE_MERGE_FAILED",
                context={"agent_id": ctx.agent_id},
            )

        stats, applied_digests = await asyncio.to_thread(self._apply_to_tree_sync, ctx, run, workdir)
        logger.info(
            "Applied agent changeset to working tree",
            extra={"agent_id": ctx.agent_id, **stats},
        )
        return stats, applied_digests

    def _apply_to_tree_sync(
        self, ctx: AgentContext, run: RunRecord, workdir: Path
    ) -> tuple[dict[str, int], dict[str, str]]:
        """Blocking strict apply of the computed changeset onto the tree."""
        written = files_deleted = 0
        applied_digests: dict[str, str] = {}
        failures: list[str] = []
        for rel in run.written:
            self._validate_rel(rel)
            source = workdir / rel
            target = self.project_root / rel
            try:
                st = source.lstat()
                target.parent.mkdir(parents=True, exist_ok=True)
                if stat.S_ISLNK(st.st_mode):
                    # Recreate the symlink as-is; never dereference it.
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    os.symlink(os.readlink(source), target)
                elif stat.S_ISREG(st.st_mode):
                    with open(source, "rb") as fin, open(target, "wb") as fout:
                        shutil.copyfileobj(fin, fout, length=1024 * 1024)
                    os.chmod(target, stat.S_IMODE(st.st_mode))
                    applied_digests[rel] = self._sha256_file(target)
                else:
                    failures.append(f"{rel}: unsupported workspace entry type")
                    continue
                written += 1
            except OSError as exc:
                failures.append(f"{rel}: {exc}")

        for rel in run.deleted:
            self._validate_rel(rel)
            target = self.project_root / rel
            try:
                if target.is_symlink() or target.is_file():
                    target.unlink()
                    files_deleted += 1
            except OSError as exc:
                failures.append(f"{rel}: {exc}")

        for rel in run.mode_changed:
            self._validate_rel(rel)
            source = workdir / rel
            target = self.project_root / rel
            try:
                if source.is_file() and not source.is_symlink() and target.is_file() and not target.is_symlink():
                    os.chmod(target, stat.S_IMODE(source.lstat().st_mode))
            except OSError as exc:
                failures.append(f"{rel}: {exc}")

        for rel in run.executable:
            self._validate_rel(rel)
            target = self.project_root / rel
            try:
                if target.is_file() and not target.is_symlink():
                    target.chmod(target.stat().st_mode | 0o111)
            except OSError as exc:
                failures.append(f"{rel}: {exc}")

        for rel in run.directories:
            self._validate_rel(rel)
            try:
                (self.project_root / rel).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                failures.append(f"{rel}: {exc}")

        if failures:
            raise WorkspaceMergeError(
                format_agent_error(
                    "Applying the changeset failed; the tree was left untouched by this accept (rollback via journal recovery)",
                    agent_id=ctx.agent_id,
                    state=ctx.state.value,
                    failures=failures,
                ),
                error_code="WORKSPACE_MERGE_FAILED",
                context={"agent_id": ctx.agent_id, "failures": failures, "failure_count": len(failures)},
            )

        return {"files_written": written, "files_deleted": files_deleted}, applied_digests

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

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
            base_manifest=result.base_manifest,
            log=result.log[-MAX_STORED_LOG_BYTES:],
            exit_code=result.exit_code,
            executable=result.executable,
            directories=result.directories,
            mode_changed=result.mode_changed,
            updated_at=time.time(),
        )
        repo = agent_fs.kv.repository(prefix="", model_type=RunRecord)
        await repo.save(RUN_KEY, record)

        ctx.files_written = len(record.written)
        ctx.files_deleted = len(record.deleted)
        claimed = set((result.submission or {}).get("changed_files", []))
        actual = set(record.written) | set(record.deleted)
        # Empty claims are cross-checked too: a claim of "nothing changed"
        # that disagrees with the computed changeset is still a mismatch
        # (review §2.7a).  The computed set is always authoritative.
        ctx.claim_mismatch = claimed != actual

    async def _transition_agent_state(self, ctx: AgentContext, new_state: AgentState) -> None:
        """Persist an agent state transition."""
        ctx.transition(new_state)
        await self._save_lifecycle_record(ctx)
        await self.persist_state()

    async def _generate_code(self, ctx: AgentContext) -> str | None:
        """Fetch and validate provider code for the agent."""
        agent_fs = await self._get_agent_workspace(ctx)
        context = {
            "agent_id": ctx.agent_id,
            "workspace": agent_fs,
            "project_root": self.project_root,
        }

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
        """Execute generated code in the bwrap sandbox over a disposable real
        workspace materialized from the canonical working tree.

        The executor snapshots the tree, materializes a copy-on-write copy,
        runs the code inside bwrap, and computes the authoritative changeset
        by diffing the workspace against the base manifest.
        """
        workdir = self.cairn_home / "workspaces" / ctx.agent_id
        executor = self.executor_factory(
            agent_id=ctx.agent_id,
            workdir=workdir,
            project_root=self.project_root,
            settings=self.executor_settings,
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
        fd, tmp_name = tempfile.mkstemp(dir=self.state_file.parent, prefix=self.state_file.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.state_file)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp_name)
            raise

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
        """Save the working tree's pre-apply content for the paths this
        accept will touch, so ``cairn undo`` can reverse it."""
        if run is None or self.bin is None:
            return
        prefix = f"undo/{ctx.agent_id}/"
        touched = sorted(set(run.written) | set(run.deleted) | set(run.mode_changed))
        for rel in touched:
            self._validate_rel(rel)

        # Blocking tree reads happen off the event loop.
        snapshots = await asyncio.to_thread(self._snapshot_paths_for_undo, touched)
        restored: list[str] = []
        removed: list[str] = []
        for rel, content in snapshots:
            if content is None:
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

    def _snapshot_paths_for_undo(self, touched: list[str]) -> list[tuple[str, bytes | None]]:
        """Read pre-apply tree content for the touched paths (blocking)."""
        snapshots: list[tuple[str, bytes | None]] = []
        for rel in touched:
            target = self.project_root / rel
            try:
                st = target.lstat()
            except OSError:
                snapshots.append((rel, None))  # did not exist before: undo = delete
                continue
            if stat.S_ISDIR(st.st_mode):
                snapshots.append((rel, None))
                continue
            if stat.S_ISLNK(st.st_mode):
                snapshots.append((rel, os.readlink(target).encode("utf-8")))
            else:
                with open(target, "rb") as handle:
                    snapshots.append((rel, handle.read()))
        return snapshots

    async def undo_accept(self, agent_id: str) -> dict[str, int]:
        """Restore the working tree to its pre-accept state for one agent.

        Runs under the same project integration lock as accept and validates
        that the accepted state is still present before reverting: if any
        touched path has been changed since the accept, undo refuses
        (``UNDO_STALE_BASE``) and keeps the undo record — it never silently
        overwrites later human edits or reports success for a partial undo
        (review §3.2).
        """
        if self.bin is None:
            raise RuntimeError("Bin workspace not initialized")
        repo = self.bin.kv.repository(prefix="", model_type=UndoRecord)
        undo = await repo.load(f"undo:{agent_id}")
        if undo is None:
            raise AgentNotFoundError(
                f"No undo record for {agent_id} (already expired or never accepted)",
                error_code="UNDO_NOT_FOUND",
            )
        for rel in undo.restore_paths + undo.delete_paths:
            self._validate_rel(rel)

        async with IntegrationLock(self.agentfs_dir / "integration.lock"):
            drifted = await self._detect_undo_drift(undo)
            if drifted:
                raise WorkspaceMergeError(
                    format_agent_error(
                        "The tree changed since this accept; undo would discard those changes",
                        agent_id=agent_id,
                        state="accepted",
                        stale_paths=drifted,
                    ),
                    error_code="UNDO_STALE_BASE",
                    context={"agent_id": agent_id, "stale_paths": drifted},
                )

            prefix = f"undo/{agent_id}/"
            restore_contents: list[tuple[str, bytes]] = []
            for rel in undo.restore_paths:
                content = await self.bin.files.read(prefix + rel, mode="binary")
                if not isinstance(content, bytes):
                    content = content.encode("utf-8")
                restore_contents.append((rel, content))

            restored, deleted = await asyncio.to_thread(
                self._undo_to_tree_sync, undo.restore_paths, undo.delete_paths, restore_contents
            )
            await repo.delete(f"undo:{agent_id}")
            return {"restored": restored, "deleted": deleted}

    async def _detect_undo_drift(self, undo: UndoRecord) -> list[str]:
        """Paths whose current tree state differs from the accepted state.

        ``applied_digests`` records the post-accept content digests of the
        written paths; a delete path's accepted state is absence.  Records
        without applied digests (pre-M5) are treated as unvalidated and pass.
        """
        if not undo.applied_digests:
            return []
        current = await asyncio.to_thread(repo.capture_manifest, self.project_root)
        drifted: list[str] = []
        for rel in undo.restore_paths:
            expected = undo.applied_digests.get(rel)
            if expected is None:
                continue
            entry = current.entry_for(rel)
            if entry is None or entry.kind != "file" or entry.digest != expected:
                drifted.append(rel)
        for rel in undo.delete_paths:
            if current.entry_for(rel) is not None:
                drifted.append(rel)
        return sorted(set(drifted))

    def _undo_to_tree_sync(
        self,
        restore_paths: list[str],
        delete_paths: list[str],
        restore_contents: list[tuple[str, bytes]],
    ) -> tuple[int, int]:
        """Blocking restore/delete against the working tree."""
        restored = 0
        for rel, content in restore_contents:
            target = self.project_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content)
            restored += 1
        deleted = 0
        for rel in delete_paths:
            target = self.project_root / rel
            try:
                if target.is_symlink() or target.is_file():
                    target.unlink()
                    deleted += 1
            except OSError:
                continue
        return restored, deleted
