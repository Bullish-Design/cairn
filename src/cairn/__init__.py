"""Cairn: sandboxed repo-agent orchestration runtime."""

from importlib.metadata import PackageNotFoundError, version

from cairn.core.exceptions import CodeProviderError
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import QueuedTask, TaskPriority, TaskQueue
from cairn.providers.providers import (
    CodeProvider,
    FileCodeProvider,
    InlineCodeProvider,
    resolve_code_provider,
)
from cairn.runtime import (
    AgentStateManager,
    WorkspaceInspector,
    WorkspaceManager,
    WorkspaceStats,
    open_workspace,
)
from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.sandbox import BwrapExecutor, SandboxExecutionError, SandboxResult
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.utils.retry import RetryStrategy, with_retry

__all__ = [
    "AgentContext",
    "AgentState",
    "AgentStateManager",
    "BwrapExecutor",
    "CairnOrchestrator",
    "CodeProvider",
    "CodeProviderError",
    "ExecutorSettings",
    "FileCodeProvider",
    "InlineCodeProvider",
    "OrchestratorSettings",
    "PathsSettings",
    "QueuedTask",
    "RetryStrategy",
    "SandboxExecutionError",
    "SandboxResult",
    "TaskPriority",
    "TaskQueue",
    "WorkspaceInspector",
    "WorkspaceManager",
    "WorkspaceStats",
    # Workspace APIs
    "open_workspace",
    "resolve_code_provider",
    "with_retry",
]

try:
    __version__ = version("cairn")
except PackageNotFoundError:  # editable/source checkout
    __version__ = "0.0.0.dev0"
