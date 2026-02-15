"""Cairn: Execution and orchestration layer for Nixbox."""

from cairn.agent import AgentContext, AgentState
from cairn.code_generator import CodeGenerator
from cairn.executor import AgentExecutor, ExecutionResult
from cairn.external_functions import ExternalFunctions, create_external_functions
from cairn.agent_tools import create_agent_tools
from cairn.orchestrator import CairnOrchestrator
from cairn.queue import QueuedTask, TaskPriority, TaskQueue
from cairn.settings import ExecutorSettings, OrchestratorSettings, PathsSettings
from cairn.retry import RetryStrategy
from cairn.signals import SignalHandler
from cairn.watcher import FileWatcher
from cairn.workspace import WorkspaceMaterializer

__all__ = [
    "AgentContext",
    "AgentExecutor",
    "AgentState",
    "CairnOrchestrator",
    "CodeGenerator",
    "ExecutionResult",
    "ExternalFunctions",
    "FileWatcher",
    "ExecutorSettings",
    "OrchestratorSettings",
    "PathsSettings",
    "QueuedTask",
    "RetryStrategy",
    "SignalHandler",
    "TaskPriority",
    "TaskQueue",
    "WorkspaceMaterializer",
    "create_external_functions",
    "create_agent_tools",
]

__version__ = "0.1.0"
