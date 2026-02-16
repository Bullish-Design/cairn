"""Typed lifecycle and submission persistence for Cairn agents."""

from __future__ import annotations

import time
from pathlib import Path
from fsdantic import VersionedKVRecord, Workspace
from pydantic import field_validator, model_validator

from cairn.agent import AgentState
from cairn.constants import LIFECYCLE_CLEANUP_MAX_AGE_SECONDS
from cairn.types import SubmissionData

AGENT_KEY_PREFIX = "agent:"
SUBMISSION_KEY = "submission"


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
        await self.repo.save(record.agent_id, record)

    async def load(self, agent_id: str) -> LifecycleRecord | None:
        return await self.repo.load(agent_id)

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
