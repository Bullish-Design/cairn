"""Typed lifecycle and submission persistence for Cairn agents."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable

from fsdantic import VersionedKVRecord, Workspace
from fsdantic.exceptions import KVConflictError
from pydantic import field_validator, model_validator

from cairn.runtime.agent import AgentState
from cairn.core.constants import (
    LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
    LIFECYCLE_MAX_RETRY_ATTEMPTS,
    LIFECYCLE_RETRY_BACKOFF_FACTOR,
    LIFECYCLE_RETRY_INITIAL_DELAY_SECONDS,
)
from cairn.utils.error_formatting import format_lifecycle_error
from cairn.core.exceptions import LifecycleError, RecoverableError, VersionConflictError
from cairn.utils.retry_utils import with_retry
from cairn.core.types import SubmissionData

logger = logging.getLogger(__name__)

AGENT_KEY_PREFIX = "agent:"
SUBMISSION_KEY = "submission"

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
    def validate_timestamps(self) -> "LifecycleRecord":
        if self.state_changed_at < self.created_at:
            raise ValueError("state_changed_at must be greater than or equal to created_at")
        return self


class SubmissionRecord(VersionedKVRecord):
    """Submission payload written by the agent runtime tools."""

    agent_id: str
    submission: SubmissionData


class LifecycleStore:
    """Manages agent lifecycle metadata in workspace KV storage."""

    def __init__(self, workspace: Workspace):
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

            if agentfs_dir is not None:
                db_path = Path(record.db_path)
                if db_path.exists():
                    db_path.unlink()

        return cleaned
