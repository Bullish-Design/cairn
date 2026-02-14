"""Runtime settings for Cairn components.

Settings are loaded from environment variables by default and can be overridden
by explicit values from constructors/CLI flags.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_MEMORY_BYTES = 1 * 1024 * 1024
_MAX_MEMORY_BYTES = 16 * 1024 * 1024 * 1024


class OrchestratorSettings(BaseSettings):
    """Settings for orchestrator scheduling/runtime behavior."""

    model_config = SettingsConfigDict(env_prefix="CAIRN_ORCHESTRATOR_", extra="ignore")

    max_concurrent_agents: int = 5
    enable_signal_polling: bool = True

    @field_validator("max_concurrent_agents")
    @classmethod
    def validate_max_concurrent_agents(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_concurrent_agents must be >= 1")
        return value


class ExecutorSettings(BaseSettings):
    """Settings for Monty execution resource limits."""

    model_config = SettingsConfigDict(env_prefix="CAIRN_EXECUTOR_", extra="ignore")

    max_execution_time: float = Field(default=60.0, description="Seconds")
    max_memory_bytes: int = 100 * 1024 * 1024
    max_recursion_depth: int = 1000

    @field_validator("max_execution_time")
    @classmethod
    def validate_max_execution_time(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("max_execution_time must be positive")
        return value

    @field_validator("max_memory_bytes")
    @classmethod
    def validate_max_memory_bytes(cls, value: int) -> int:
        if not (_MIN_MEMORY_BYTES <= value <= _MAX_MEMORY_BYTES):
            raise ValueError(
                f"max_memory_bytes must be between {_MIN_MEMORY_BYTES} and {_MAX_MEMORY_BYTES}"
            )
        return value

    @field_validator("max_recursion_depth")
    @classmethod
    def validate_max_recursion_depth(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_recursion_depth must be >= 1")
        return value


class PathsSettings(BaseSettings):
    """Optional path settings for project and Cairn home."""

    model_config = SettingsConfigDict(env_prefix="CAIRN_PATHS_", extra="ignore")

    project_root: Path | None = None
    cairn_home: Path | None = None
