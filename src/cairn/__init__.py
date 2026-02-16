"""Cairn: Execution and orchestration layer for Nixbox."""

from cairn.agent import AgentContext, AgentState
from cairn.external_functions import CairnExternalFunctions, create_external_functions
from cairn.orchestrator import CairnOrchestrator
from cairn.providers import CodeProvider, CodeProviderError, FileCodeProvider, InlineCodeProvider
from cairn.queue import QueuedTask, TaskPriority, TaskQueue
from cairn.retry import RetryStrategy
from cairn.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.signals import SignalHandler
from cairn.watcher import FileWatcher

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
    "ExecutorSettings",
    "OrchestratorSettings",
    "PathsSettings",
    "QueuedTask",
    "RetryStrategy",
    "SignalHandler",
    "TaskPriority",
    "TaskQueue",
    "create_external_functions",
]

__version__ = "0.1.0"
