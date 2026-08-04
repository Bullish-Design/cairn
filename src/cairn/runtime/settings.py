"""Runtime settings for Cairn components.

Settings are loaded from environment variables by default and can be overridden
by explicit values from constructors/CLI flags.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cairn.core.constants import (
    DEFAULT_MAX_QUEUE_SIZE,
    DEFAULT_MAX_SYNC_FILE_BYTES,
    DEFAULT_MAX_WORKSPACE_BYTES,
    MAX_WORKSPACE_CACHE_SIZE,
)

_MIN_MEMORY_BYTES = 1 * 1024 * 1024
_MAX_MEMORY_BYTES = 16 * 1024 * 1024 * 1024


class OrchestratorSettings(BaseSettings):
    """Settings for orchestrator scheduling/runtime behavior."""

    model_config = SettingsConfigDict(env_prefix="CAIRN_ORCHESTRATOR_", extra="ignore")

    max_concurrent_agents: int = 5
    max_queue_size: int = Field(default=DEFAULT_MAX_QUEUE_SIZE, description="Maximum queued tasks")
    workspace_cache_size: int = Field(default=MAX_WORKSPACE_CACHE_SIZE, description="Workspace cache size")
    enable_signal_polling: bool = True
    sync_project_on_start: bool = Field(default=True, description="Mirror the project tree into stable at startup")
    max_sync_file_bytes: int = Field(
        default=DEFAULT_MAX_SYNC_FILE_BYTES, description="Largest file the watcher/initial sync will import"
    )
    extra_ignore_dirs: list[str] = Field(
        default_factory=list, description="Additional directory names to exclude from the project sync"
    )

    requeue_interrupted: bool = False
    start_worker_on_init: bool = Field(
        default=True,
        description="Start the worker loop during initialize(); disable for tests/embedders that schedule manually",
    )
    max_content_bytes: int | None = Field(
        default=None,
        description="Optional cap on workspace write payload sizes (fsdantic CONTENT_TOO_LARGE)",
    )

    @field_validator("max_concurrent_agents")
    @classmethod
    def validate_max_concurrent_agents(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_concurrent_agents must be >= 1")
        return value

    @field_validator("max_queue_size")
    @classmethod
    def validate_max_queue_size(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_queue_size must be >= 0")
        return value

    @field_validator("workspace_cache_size")
    @classmethod
    def validate_workspace_cache_size(cls, value: int) -> int:
        if value < 1:
            raise ValueError("workspace_cache_size must be >= 1")
        return value


class ExecutorSettings(BaseSettings):
    """Settings for sandboxed execution resource limits and runtime."""

    model_config = SettingsConfigDict(env_prefix="CAIRN_EXECUTOR_", extra="ignore")

    max_execution_time: float = Field(default=60.0, description="Seconds")
    max_memory_bytes: int = 100 * 1024 * 1024
    max_recursion_depth: int = 1000
    max_output_file_bytes: int = Field(
        default=64 * 1024 * 1024,
        description="RLIMIT_FSIZE: largest single file the sandbox may create",
    )
    max_processes: int = Field(
        default=64, description="RLIMIT_NPROC: process/thread cap inside the sandbox"
    )
    max_open_files: int = Field(
        default=1024, description="RLIMIT_NOFILE: open file descriptor cap"
    )
    max_workspace_bytes: int = Field(
        default=DEFAULT_MAX_WORKSPACE_BYTES,
        description="Post-run cap on total materialized workspace size",
    )
    bwrap_path: str | None = Field(default=None, description="Path to the bubblewrap binary")
    python_path: str | None = Field(default=None, description="Python interpreter to run inside the sandbox")
    sandbox_closure_path: str | None = Field(
        default=None,
        description="File listing the sandbox interpreter's Nix store closure (one path per line)",
    )
    sandbox_uid: int = Field(default=65534, description="UID inside the sandbox (nobody)")
    sandbox_gid: int = Field(default=65534, description="GID inside the sandbox (nobody)")
    runtime_mounts: list[tuple[str, str]] | None = Field(
        default=None,
        description="Read-only runtime bind mounts (src, dest) for the sandbox fallback",
    )

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
            raise ValueError(f"max_memory_bytes must be between {_MIN_MEMORY_BYTES} and {_MAX_MEMORY_BYTES}")
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
