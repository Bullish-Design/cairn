"""Command pattern implementation for Cairn orchestrator operations.

This module defines the command objects used to communicate with the orchestrator,
implementing a command pattern for operation dispatching. Commands are immutable
dataclasses that encapsulate operation parameters.

Supported Commands:
    - QueueCommand: Queue a new agent task
    - AcceptCommand: Accept an agent's changes
    - RejectCommand: Reject an agent's changes
    - ListCommand: List agents with filtering
    - InspectCommand: Inspect agent details
    - StatusCommand: Get orchestrator status
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Literal, Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from cairn.orchestrator.queue import TaskPriority


class CommandType(str, Enum):
    """Supported high-level Cairn command operations."""

    QUEUE = "queue"
    ACCEPT = "accept"
    REJECT = "reject"
    STATUS = "status"
    LIST_AGENTS = "list_agents"


class _BaseCommandModel(BaseModel):
    """Base command model used across CLI and signal processing."""

    model_config = ConfigDict(extra="ignore")

    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class QueueCommand(_BaseCommandModel):
    type: Literal[CommandType.QUEUE] = CommandType.QUEUE
    task: str = Field(min_length=1)
    priority: TaskPriority
    spawn_alias: bool = Field(default=False, exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def _default_priority(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data

        parsed = dict(data)
        if parsed.get("priority") is None:
            parsed["priority"] = TaskPriority.HIGH if parsed.get("spawn_alias") else TaskPriority.NORMAL
        return parsed


class AcceptCommand(_BaseCommandModel):
    type: Literal[CommandType.ACCEPT] = CommandType.ACCEPT
    agent_id: str = Field(min_length=1)


class RejectCommand(_BaseCommandModel):
    type: Literal[CommandType.REJECT] = CommandType.REJECT
    agent_id: str = Field(min_length=1)


class StatusCommand(_BaseCommandModel):
    type: Literal[CommandType.STATUS] = CommandType.STATUS
    agent_id: str = Field(min_length=1)


class ListAgentsCommand(_BaseCommandModel):
    type: Literal[CommandType.LIST_AGENTS] = CommandType.LIST_AGENTS


CommandEnvelope: TypeAlias = Annotated[
    QueueCommand | AcceptCommand | RejectCommand | StatusCommand | ListAgentsCommand,
    Field(discriminator="type"),
]

CairnCommand = CommandEnvelope
_COMMAND_ENVELOPE_ADAPTER = TypeAdapter(CommandEnvelope)


@dataclass(slots=True)
class CommandResult:
    """Normalized result returned after orchestrator command dispatch."""

    command_type: CommandType
    ok: bool = True
    agent_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def _parse_command_type(command_type: CommandType | str) -> tuple[CommandType, bool]:
    if isinstance(command_type, CommandType):
        return command_type, False

    normalized = command_type.strip().lower().replace("-", "_")
    if normalized == "spawn":
        return CommandType.QUEUE, True

    try:
        return CommandType(normalized), False
    except ValueError as exc:
        raise ValueError(f"unsupported command type: {command_type}") from exc


def parse_command_payload(
    command_type: CommandType | str,
    payload: Mapping[str, Any] | None = None,
) -> CommandEnvelope:
    """Parse/validate incoming command data and normalize command defaults."""

    data = dict(payload or {})
    command, is_spawn_alias = _parse_command_type(command_type)
    data["type"] = command

    if command is CommandType.QUEUE and is_spawn_alias:
        data["spawn_alias"] = True

    metadata_raw = data.get("metadata")
    if not isinstance(metadata_raw, Mapping):
        data["metadata"] = {}

    try:
        return _COMMAND_ENVELOPE_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
