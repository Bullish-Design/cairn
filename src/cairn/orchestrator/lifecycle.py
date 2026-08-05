"""Typed lifecycle and submission persistence for Cairn agents."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal

from fsdantic import VersionedKVRecord, Workspace
from fsdantic.exceptions import KVConflictError
from pydantic import Field, ValidationError, field_validator, model_validator

from cairn.core.constants import (
    LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
    LIFECYCLE_MAX_RETRY_ATTEMPTS,
    LIFECYCLE_RETRY_BACKOFF_FACTOR,
    LIFECYCLE_RETRY_INITIAL_DELAY_SECONDS,
)
from cairn.core.exceptions import AgentNotFoundError, LifecycleError, RecoverableError, VersionConflictError
from cairn.core.types import SubmissionData
from cairn.runtime.agent import AgentState
from cairn.runtime.repo import ManifestEntry
from cairn.runtime.workspace_cache import WorkspaceCache
from cairn.utils.error_formatting import format_lifecycle_error
from cairn.utils.retry import with_retry

logger = logging.getLogger(__name__)

AGENT_KEY_PREFIX = "agent:"
SUBMISSION_KEY = "submission"
RUN_KEY = "run"
ACCEPTING_KEY_PREFIX = "accepting:"
COMMAND_KEY_PREFIX = "cmd:"

LIFECYCLE_MIRROR_NAME = "lifecycle.json"


def lifecycle_mirror_path(cairn_home: Path) -> Path:
    """Location of the daemon's lifecycle mirror (the CLI's read path)."""
    return Path(cairn_home) / "state" / LIFECYCLE_MIRROR_NAME


LIFECYCLE_RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    RecoverableError,
    VersionConflictError,
    TimeoutError,
    ConnectionError,
    OSError,
)


class LifecycleRecord(VersionedKVRecord):
    """Canonical lifecycle metadata stored in the lifecycle workspace."""

    agent_id: str
    task: str
    priority: int
    state: AgentState
    state_changed_at: float
    db_path: str
    submission: SubmissionData | None = None
    error: str | None = None
    version: int = 0
    accept_stats: dict[str, int] | None = None
    files_written: int = 0
    files_deleted: int = 0
    claim_mismatch: bool = False
    # Mirror-only enrichment (never written to bin.db): the ground-truth path
    # lists and run log the CLI surfaces via the lifecycle mirror.
    run_written: list[str] | None = None
    run_deleted: list[str] | None = None
    run_log: str | None = None

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("agent_id must be non-empty")
        return value

    @field_validator("state", mode="before")
    @classmethod
    def validate_state(cls, value: AgentState | str) -> AgentState:
        return AgentState(value)

    @model_validator(mode="after")
    def validate_timestamps(self) -> LifecycleRecord:
        if self.state_changed_at < self.created_at:
            raise ValueError("state_changed_at must be greater than or equal to created_at")
        return self


class SubmissionRecord(VersionedKVRecord):
    """Submission payload written by the agent runtime tools."""

    agent_id: str
    submission: SubmissionData


class RunRecord(VersionedKVRecord):
    """Ground truth about what the sandbox actually did."""

    agent_id: str
    written: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    base_hashes: dict[str, str] = Field(default_factory=dict)
    # Full fidelity base entries (kind/digest/mode/symlink-target) for every
    # touched path, including explicit absent states (a touched path with no
    # entry here did not exist at run start).  This is what accept revalidates
    # against the current tree (review §2.6, §3.3).
    base_manifest: dict[str, ManifestEntry] = Field(default_factory=dict)
    log: str = ""
    exit_code: int = 0
    executable: list[str] = Field(default_factory=list)
    directories: list[str] = Field(default_factory=list)
    mode_changed: list[str] = Field(default_factory=list)


class UndoRecord(VersionedKVRecord):
    """Pre-accept snapshot of the working tree for one agent, enabling `cairn undo`.

    ``applied_digests`` records the post-accept content digests of the
    written paths so ``undo`` can validate that the accepted state is still
    in the tree before reverting (it never overwrites later human edits).
    """

    agent_id: str
    restore_paths: list[str] = Field(default_factory=list)
    delete_paths: list[str] = Field(default_factory=list)
    created_at: float = 0.0
    applied_digests: dict[str, str] = Field(default_factory=dict)


class AcceptingRecord(VersionedKVRecord):
    """Durable transaction journal for an in-flight accept.

    Written before any tree mutation and removed after the apply completes;
    a leftover record on startup means the process died mid-apply, and
    recovery rolls the tree back via the undo snapshot (fail-safe).
    """

    agent_id: str
    started_at: float = 0.0


class CommandRecord(VersionedKVRecord):
    """Idempotency + result record for one transport command (review §3.1).

    Keyed by the client-generated ``command_id``: a retried request with the
    same id returns the recorded result instead of re-executing; a "pending"
    record on startup was in flight when the daemon died and is failed by
    recovery.
    """

    command_id: str
    command_type: str
    status: Literal["pending", "done", "failed"] = "pending"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = 0.0
    completed_at: float | None = None


class LifecycleStore:
    """Manages agent lifecycle metadata in workspace KV storage.

    Same-process read-modify-write sequences (``update_atomic``) are
    serialized with ``Workspace.serialized()`` so concurrent updates within
    one process do not trip version conflicts against each other; conflicts
    from other connections/processes are still detected by the repository's
    SQL compare-and-set and retried.
    """

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.repo = workspace.kv.repository(prefix=AGENT_KEY_PREFIX, model_type=LifecycleRecord)

    async def save(self, record: LifecycleRecord) -> None:
        try:
            await self._save_with_retry(record)
        except LIFECYCLE_RETRY_EXCEPTIONS:
            raise
        except Exception as exc:
            raise LifecycleError(f"Failed to save lifecycle record for {record.agent_id}") from exc

    @with_retry(
        max_attempts=3,
        initial_delay=0.0,
        max_delay=0.0,
        retry_exceptions=LIFECYCLE_RETRY_EXCEPTIONS,
    )
    async def _save_with_retry(self, record: LifecycleRecord) -> None:
        existing = None
        if hasattr(self.repo, "load"):
            existing = await self.repo.load(record.agent_id)

        if existing:
            if existing.version != record.version:
                raise VersionConflictError(
                    format_lifecycle_error(
                        "Version conflict - record was modified concurrently",
                        agent_id=record.agent_id,
                        version=record.version,
                        expected_version=existing.version,
                    ),
                    error_code="VERSION_CONFLICT",
                    context={
                        "agent_id": record.agent_id,
                        "expected_version": existing.version,
                        "provided_version": record.version,
                    },
                )
            record.created_at = existing.created_at
        elif record.version == 0:
            record.version = 1

        try:
            await self.repo.save(record.agent_id, record)
        except KVConflictError as exc:
            expected_version = getattr(exc, "expected_version", None)
            actual_version = getattr(exc, "actual_version", None)
            raise VersionConflictError(
                format_lifecycle_error(
                    "Version conflict - record was modified concurrently",
                    agent_id=record.agent_id,
                    version=record.version,
                    expected_version=expected_version,
                    actual_version=actual_version,
                ),
                error_code="VERSION_CONFLICT",
                context={
                    "agent_id": record.agent_id,
                    "expected_version": expected_version,
                    "actual_version": actual_version,
                },
            ) from exc

    async def load(self, agent_id: str) -> LifecycleRecord | None:
        return await self.repo.load(agent_id)

    async def update_atomic(
        self,
        agent_id: str,
        update_fn: Callable[[LifecycleRecord], Any],
        max_retries: int = LIFECYCLE_MAX_RETRY_ATTEMPTS,
    ) -> LifecycleRecord:
        # Same-process serialization: concurrent update_atomic calls on this
        # workspace (multiple agents transitioning in parallel) are serialized
        # by the per-workspace lock, so the load->update->save cycle below does
        # not fight itself.  Conflicts from other connections or processes are
        # still detected by the repository CAS (KVConflictError) and retried.
        async with self.workspace.serialized():
            for attempt in range(1, max_retries + 1):
                record = await self.load(agent_id)
                if record is None:
                    raise LifecycleError(
                        f"Cannot update non-existent record: {agent_id}",
                        error_code="LIFECYCLE_NOT_FOUND",
                        context={"agent_id": agent_id},
                    )

                update_fn(record)

                try:
                    await self.save(record)
                    return record
                except VersionConflictError:
                    if attempt >= max_retries:
                        logger.error(
                            "Failed to update lifecycle after retries",
                            extra={"agent_id": agent_id, "attempts": max_retries},
                        )
                        raise

                    delay = LIFECYCLE_RETRY_INITIAL_DELAY_SECONDS * (LIFECYCLE_RETRY_BACKOFF_FACTOR ** (attempt - 1))
                    logger.debug(
                        "Version conflict on lifecycle update; retrying",
                        extra={"agent_id": agent_id, "attempt": attempt, "delay": delay},
                    )
                    await asyncio.sleep(delay)

        raise VersionConflictError("Unexpected retry exhaustion")

    async def delete(self, agent_id: str) -> None:
        await self.repo.delete(agent_id)

    async def list_all(self) -> list[LifecycleRecord]:
        return await self.repo.list_all()

    async def list_active(self) -> list[LifecycleRecord]:
        all_records = await self.list_all()
        terminal_states = {AgentState.ACCEPTED, AgentState.REJECTED}
        return [record for record in all_records if record.state not in terminal_states]

    async def cleanup_old(
        self,
        max_age_seconds: float = LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
        agentfs_dir: Path | None = None,
        cache: WorkspaceCache | None = None,
        cairn_home: Path | None = None,
    ) -> int:
        cutoff = time.time() - max_age_seconds
        cleaned = 0

        terminal_states = {AgentState.ACCEPTED, AgentState.REJECTED, AgentState.ERRORED}

        for record in await self.list_all():
            if record.state not in terminal_states:
                continue
            if record.state_changed_at >= cutoff:
                continue

            await self.delete(record.agent_id)
            cleaned += 1

            # Drop the accept-undo snapshot for the same agent (P2.3): the
            # undo window is bounded by the same retention schedule.
            undo_repo = self.workspace.kv.repository(prefix="", model_type=UndoRecord)
            with suppress(Exception):
                await undo_repo.delete(f"undo:{record.agent_id}")
            undo_prefix = f"undo/{record.agent_id}/"
            with suppress(Exception):
                entries = await self.workspace.files.search(undo_prefix + "**/*")
                for entry in entries:
                    with suppress(Exception):
                        await self.workspace.files.remove(entry)

            if agentfs_dir is not None:
                db_path = Path(record.db_path)
                # Drop the workspace cache entry first so the open handle is
                # closed before the file is unlinked (P4.8).
                if cache is not None:
                    with suppress(Exception):
                        await cache.remove(str(db_path))
                if db_path.exists():
                    db_path.unlink()
                if cairn_home is not None:
                    workdir = Path(cairn_home) / "workspaces" / record.agent_id
                    if workdir.exists():
                        shutil.rmtree(workdir, ignore_errors=True)

        return cleaned


@asynccontextmanager
async def open_lifecycle_readonly(cairn_home: Path) -> AsyncIterator[ReadonlyLifecycleStore]:
    """Yield a read-only view of lifecycle records, safe alongside a daemon.

    pyturso 0.7.2 takes an exclusive file lock even for read-only opens, so a
    second process cannot open ``bin.db`` through fsdantic while the daemon
    holds it.  The daemon therefore maintains a JSON mirror
    (``state/lifecycle.json``) after every lifecycle mutation, and CLI queries
    read that instead.
    """
    path = lifecycle_mirror_path(cairn_home)
    if not path.exists():
        raise AgentNotFoundError(
            "No Cairn state found - has the orchestrator ever run?",
            error_code="LIFECYCLE_STORE_MISSING",
        )
    yield ReadonlyLifecycleStore(path)


class ReadonlyLifecycleStore:
    """Read-only lifecycle queries over the daemon's lifecycle mirror.

    ``load``/``list_all`` are async but delegate the file I/O to a worker
    thread so they never block the event loop.
    """

    def __init__(self, mirror: Path) -> None:
        self.mirror = Path(mirror)

    async def load(self, agent_id: str) -> LifecycleRecord | None:
        return await asyncio.to_thread(self._load_sync, agent_id)

    async def list_all(self) -> list[LifecycleRecord]:
        return await asyncio.to_thread(self._list_all_sync)

    def _read(self) -> dict[str, LifecycleRecord]:
        try:
            payload = json.loads(self.mirror.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        records: dict[str, LifecycleRecord] = {}
        if isinstance(payload, dict):
            for agent_id, data in payload.items():
                try:
                    records[agent_id] = LifecycleRecord.model_validate(data)
                except ValidationError:
                    continue
        return records

    def _load_sync(self, agent_id: str) -> LifecycleRecord | None:
        return self._read().get(agent_id)

    def _list_all_sync(self) -> list[LifecycleRecord]:
        return list(self._read().values())
