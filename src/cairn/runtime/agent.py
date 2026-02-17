"""Agent lifecycle state and context models."""

from __future__ import annotations

import time
from enum import Enum
from pathlib import Path

from fsdantic import Workspace
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cairn.orchestrator.queue import TaskPriority
from cairn.core.types import ExecutionResult, SubmissionData


class AgentState(str, Enum):
    """Agent lifecycle states from queueing through completion."""

    QUEUED = "queued"
    GENERATING = "generating"
    EXECUTING = "executing"
    SUBMITTING = "submitting"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERRORED = "errored"


class AgentContext(BaseModel):
    """Runtime metadata for an agent task lifecycle."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent_id: str
    task: str
    priority: TaskPriority
    state: AgentState
    agent_db_path: Path
    agent_fs: Workspace | None = None
    generated_code: str | None = None
    execution_result: ExecutionResult | None = None
    submission: SubmissionData | None = None
    error: str | None = None
    created_at: float = Field(default_factory=time.time)
    state_changed_at: float = Field(default_factory=time.time)

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("agent_id must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_timestamps(self) -> AgentContext:
        if self.state_changed_at < self.created_at:
            raise ValueError("state_changed_at must be greater than or equal to created_at")
        return self

    def transition(self, new_state: AgentState) -> None:
        """Transition state and update the lifecycle timestamp."""
        self.state = new_state
        self.state_changed_at = time.time()
