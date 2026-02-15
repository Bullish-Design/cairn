"""Resource guard models and runtime metrics for Monty executions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .types import ResourceLimits


class ResourceGuard(BaseModel):
    """Validated resource limits that can be sent to Monty."""

    model_config = ConfigDict(extra="forbid")

    max_allocations: int | None = Field(default=None, ge=1)
    max_duration_secs: float | None = Field(default=None, gt=0)
    max_memory: int | None = Field(default=None, ge=1024)
    gc_interval: int | None = Field(default=None, ge=1)
    max_recursion_depth: int | None = Field(default=None, ge=1)

    def to_monty_limits(self) -> ResourceLimits:
        """Return Monty-compatible limits payload with unset fields omitted."""
        return self.model_dump(exclude_none=True)


class ResourceUsageMetrics(BaseModel):
    """Runtime metrics exposed after execution for observability/debugging."""

    model_config = ConfigDict(extra="allow")

    max_allocations: int | None = None
    max_duration_secs: float | None = None
    max_memory: int | None = None
    gc_interval: int | None = None
    max_recursion_depth: int | None = None
    exceeded: list[str] = Field(default_factory=list)

    @classmethod
    def from_runtime_payload(cls, payload: Any) -> ResourceUsageMetrics:
        if isinstance(payload, cls):
            return payload
        if isinstance(payload, dict):
            return cls.model_validate(payload)
        return cls()
