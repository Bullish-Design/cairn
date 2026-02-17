"""Core Cairn orchestrator for agent lifecycle management."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import cast

from fsdantic import Fsdantic, MergeStrategy, Workspace
import grail

from cairn.runtime.agent import AgentContext, AgentState
from cairn.utils.error_formatting import format_agent_error
from cairn.runtime.external_functions import create_external_functions
from cairn.providers.providers import CodeProvider, FileCodeProvider
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
    TimeoutError as CairnTimeoutError,
    VersionConflictError,
    WorkspaceMergeError,
)
from cairn.orchestrator.lifecycle import LifecycleRecord, LifecycleStore, SUBMISSION_KEY, SubmissionRecord
from cairn.orchestrator.queue import TaskPriority, TaskQueue
from cairn.runtime.resource_limits import ResourceLimiter, run_with_timeout
from cairn.utils.retry_utils import with_retry
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.orchestrator.signals import SignalHandler
from cairn.core.types import AgentSummary, GrailCheckResult, GrailScript, ToolsFactory
from cairn.watcher.watcher import FileWatcher
from cairn.runtime.workspace_cache import WorkspaceCache
from cairn.runtime.workspace_manager import WorkspaceManager


logger = logging.getLogger(__name__)

_grail_errors: list[type[Exception]] = [grail.GrailExecutionError]
_execution_error = getattr(grail, "ExecutionError", None)
if _execution_error is None:
    _execution_error = grail.GrailExecutionError
    setattr(grail, "ExecutionError", _execution_error)
if isinstance(_execution_error, type) and issubclass(_execution_error, Exception):
    _grail_errors.append(_execution_error)
_input_error = getattr(grail, "InputError", None)
if isinstance(_input_error, type) and issubclass(_input_error, Exception):
    _grail_errors.append(_input_error)
GRAIL_EXECUTION_ERRORS = tuple(dict.fromkeys(_grail_errors))


def _load_grail_script(pym_path: Path) -> GrailScript:
    """Load a Grail script using legacy and current loader entry points."""

    script_path = str(pym_path)

    # Grail 1.x exposed a top-level `load` function.
    legacy_loader = getattr(grail, "load", None)
    if callable(legacy_loader):
        return cast(GrailScript, legacy_loader(script_path))

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
            return cast(GrailScript, loader(script_path))

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
        tools_factory: ToolsFactory | None = None,
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
        self.tools_factory = tools_factory or create_external_functions

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
            except asyncio.TimeoutError:
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

    async def accept_agent(self, agent_id: str) -> None:
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

        ctx.transition(AgentState.ACCEPTED)
        await self._save_lifecycle_record(ctx)
        await self.trash_agent(agent_id)

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
        except GRAIL_EXECUTION_ERRORS as exc:
            await self._handle_agent_error(ctx, exc)
        except (ResourceLimitError, CairnTimeoutError) as exc:
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

        script = await self._validate_code(ctx, generated)
        if script is None:
            return

        await self._execute_script(ctx, script)
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

    async def _validate_code(self, ctx: AgentContext, generated: str) -> GrailScript | None:
        """Write and validate the Grail script for generated code."""
        grail_dir = self.project_root / ".grail" / "agents" / ctx.agent_id
        grail_dir.mkdir(parents=True, exist_ok=True)
        pym_path = grail_dir / "task.pym"
        pym_path.write_text(generated, encoding="utf-8")

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
            await self._transition_agent_state(ctx, AgentState.ERRORED)
            return None

        return script

    async def _execute_script(self, ctx: AgentContext, script: GrailScript) -> None:
        """Execute the Grail script within resource limits."""
        if self.stable is None:
            raise RuntimeError("Stable workspace not initialized")

        agent_fs = ctx.agent_fs
        if agent_fs is None:
            agent_fs = await self._get_agent_workspace(ctx)

        tools = self.tools_factory(ctx.agent_id, agent_fs, self.stable)
        limiter = ResourceLimiter(
            timeout_seconds=self.executor_settings.max_execution_time,
            max_memory_bytes=self.executor_settings.max_memory_bytes,
        )

        async with limiter.limit():
            await run_with_timeout(
                script.run(inputs={"task_description": ctx.task}, externals=tools),
                timeout_seconds=self.executor_settings.max_execution_time,
            )

    async def _submit_results(self, ctx: AgentContext) -> None:
        """Load submission metadata and materialize preview workspace."""
        agent_fs = ctx.agent_fs
        if agent_fs is None:
            agent_fs = await self._get_agent_workspace(ctx)

        submission_repo = agent_fs.kv.repository(prefix="", model_type=SubmissionRecord)
        submission_record = await submission_repo.load(SUBMISSION_KEY)
        ctx.submission = submission_record.submission if submission_record else None

        preview_dir = self.cairn_home / "workspaces" / ctx.agent_id
        await agent_fs.materialize.to_disk(
            target_path=preview_dir,
            base=self.stable,
            clean=True,
            allow_root=self.cairn_home / "workspaces",
        )

    async def _handle_agent_error(self, ctx: AgentContext | None, exc: Exception) -> None:
        """Record agent failure details and persist lifecycle state."""
        if ctx is None:
            return

        ctx.error = str(exc)
        ctx.transition(AgentState.ERRORED)
        await self._save_lifecycle_record(ctx)

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
        if self.lifecycle is None:
            return

        db_path = ctx.agent_db_path
        if not db_path.exists():
            bin_path = self.agentfs_dir / f"bin-{ctx.agent_id}.db"
            if bin_path.exists():
                db_path = bin_path

        existing = await self.lifecycle.load(ctx.agent_id)
        if existing:
            try:
                await self.lifecycle.update_atomic(
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
            await self.lifecycle.save(record)

        await _persist_record()

    def _get_agent(self, agent_id: str) -> AgentContext:
        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            raise KeyError(f"Unknown agent_id: {agent_id}")
        return ctx

    @staticmethod
    def _format_grail_errors(check_result: GrailCheckResult) -> str:
        errors = getattr(check_result, "errors", None)
        if errors:
            return "Grail validation failed: " + "; ".join(str(error) for error in errors)
        return "Grail validation failed"
