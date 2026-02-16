"""Exception hierarchy for Cairn operations.

This module defines the complete exception hierarchy for Cairn, providing
structured error handling with error codes for programmatic handling.
"""

from __future__ import annotations

from typing import Any


class CairnError(Exception):
    """Base exception for all Cairn operations.

    Attributes:
        error_code: Machine-readable error code for programmatic handling
        message: Human-readable error message
        context: Additional context information
    """

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self._default_error_code()
        self.context = context or {}

    def _default_error_code(self) -> str:
        """Generate default error code from class name."""
        return self.__class__.__name__.upper()

    def __str__(self) -> str:
        """Format error with code and context."""
        base = f"[{self.error_code}] {self.message}"
        if self.context:
            context_str = ", ".join(f"{key}={value}" for key, value in self.context.items())
            return f"{base} ({context_str})"
        return base


class RecoverableError(CairnError):
    """Errors that can be retried with potential for success.

    These errors indicate transient failures that may succeed on retry,
    such as network timeouts, temporary file locks, or resource unavailability.
    """


class FatalError(CairnError):
    """Errors that cannot be recovered through retry.

    These errors indicate permanent failures that require intervention,
    such as configuration errors, invalid input, or system constraints.
    """


class AgentError(CairnError):
    """Errors related to agent lifecycle and execution."""


class AgentStateError(AgentError):
    """Invalid agent state transition attempted."""


class AgentExecutionError(AgentError):
    """Error during agent code generation or execution."""


class ValidationError(FatalError, ValueError):
    """Input validation failures."""


class PathValidationError(ValidationError):
    """Path validation failure (traversal, absolute path, etc.)."""


class ResourceError(CairnError):
    """Resource exhaustion or limit errors."""


class ResourceLimitError(ResourceError):
    """Resource limit exceeded (memory, time, disk space)."""


class WorkspaceError(RecoverableError):
    """Errors related to workspace operations."""


class WorkspaceMergeError(WorkspaceError):
    """Workspace merge operation failed."""


class LifecycleError(CairnError):
    """Errors related to lifecycle record persistence."""


class VersionConflictError(LifecycleError, RecoverableError):
    """Optimistic locking version conflict - can be retried."""


class ProviderError(CairnError):
    """Base class for code provider errors."""


class CodeProviderError(ProviderError):
    """Legacy exception - kept for backward compatibility."""


class PluginError(FatalError):
    """Plugin loading or execution errors."""


class ConfigurationError(FatalError):
    """Configuration or settings errors."""


class SecurityError(FatalError):
    """Security-related errors (secrets, sandbox violations)."""


class SecretsDetectedError(SecurityError):
    """Secrets detected in agent submission."""


class TimeoutError(ResourceLimitError, RecoverableError):
    """Operation exceeded time limit."""
