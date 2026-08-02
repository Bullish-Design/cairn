"""Cairn: Execution and orchestration layer for Nixbox."""

from cairn.core.exceptions import CodeProviderError
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import QueuedTask, TaskPriority, TaskQueue
from cairn.orchestrator.signals import SignalHandler
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
from cairn.utils.retry import RetryStrategy
from cairn.utils.retry_utils import with_retry
from cairn.watcher.watcher import FileWatcher

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
    "FileWatcher",
    "InlineCodeProvider",
    "OrchestratorSettings",
    "PathsSettings",
    "QueuedTask",
    "RetryStrategy",
    "SandboxExecutionError",
    "SandboxResult",
    "SignalHandler",
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

__version__ = "0.2.1"
