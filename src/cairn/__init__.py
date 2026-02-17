"""Cairn: Execution and orchestration layer for Nixbox."""

from cairn.runtime.agent import AgentContext, AgentState
from cairn.runtime.external_functions import CairnExternalFunctions, create_external_functions
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.core.exceptions import CodeProviderError
from cairn.providers.providers import (
    CodeProvider,
    FileCodeProvider,
    InlineCodeProvider,
    resolve_code_provider,
)
from cairn.orchestrator.queue import QueuedTask, TaskPriority, TaskQueue
from cairn.utils.retry import RetryStrategy
from cairn.utils.retry_utils import with_retry
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.orchestrator.signals import SignalHandler
from cairn.watcher.watcher import FileWatcher

__all__ = [
    "AgentContext",
    "AgentState",
    "CairnExternalFunctions",
    "CairnOrchestrator",
    "CodeProvider",
    "CodeProviderError",
    "FileCodeProvider",
    "FileWatcher",
    "InlineCodeProvider",
    "resolve_code_provider",
    "ExecutorSettings",
    "OrchestratorSettings",
    "PathsSettings",
    "QueuedTask",
    "RetryStrategy",
    "with_retry",
    "SignalHandler",
    "TaskPriority",
    "TaskQueue",
    "create_external_functions",
]

__version__ = "0.1.0"
