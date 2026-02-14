"""Pydantic models and key helpers for AgentFS KV persistence."""

from __future__ import annotations

from typing import Any

from fsdantic import VersionedKVRecord
from pydantic import field_validator, model_validator

from cairn.agent import AgentState

AGENT_KEY_PREFIX = "agent:"
AGENT_KEY_TEMPLATE = "agent:{agent_id}"
SUBMISSION_KEY = "submission"


def agent_key(agent_id: str) -> str:
    """Build lifecycle KV key for an agent ID."""
    return AGENT_KEY_TEMPLATE.format(agent_id=agent_id)


class LifecycleRecord(VersionedKVRecord):
    """Canonical lifecycle record for an agent.

    Inherits automatic timestamp tracking (created_at, updated_at) and versioning
    from VersionedKVRecord. The state_changed_at field is kept for backward compatibility
    and more explicit state transition tracking.
    """

    agent_id: str
    task: str
    priority: int
    state: AgentState
    state_changed_at: float
    db_path: str
    submission: dict[str, Any] | None = None
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
    def validate_timestamps(self) -> LifecycleRecord:
        if self.state_changed_at < self.created_at:
            raise ValueError("state_changed_at must be greater than or equal to created_at")
        return self


class SubmissionRecord(VersionedKVRecord):
    """Typed payload stored in per-agent KV for review submission.

    Inherits automatic timestamp tracking from VersionedKVRecord.
    """

    agent_id: str
    submission: dict[str, Any]
