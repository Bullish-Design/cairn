"""Type definitions for Cairn operations.

This module provides TypedDict definitions and type aliases for improved
type safety throughout the Cairn codebase.
"""

from __future__ import annotations

from typing import Any, Generic, Protocol, TypeAlias, TypedDict, TypeVar


class SearchContentMatchData(TypedDict):
    """Search match data returned by search_content."""

    file: str
    line: int
    text: str


class SubmissionData(TypedDict):
    """Submission payload stored for agent review."""

    summary: str
    changed_files: list[str]
    submitted_at: float


class AgentSummary(TypedDict):
    """Summary payload for list_agents responses."""

    state: str
    task: str
    priority: int


ExecutionResult: TypeAlias = dict[str, Any]


class FileEntryProtocol(Protocol):
    """Protocol for file entries returned from workspace queries."""

    path: str
    content: str | bytes | None


T = TypeVar("T")


class Result(Generic[T]):
    """Generic result wrapper for operations that may fail."""

    def __init__(self, value: T | None = None, error: str | None = None) -> None:
        self._value = value
        self._error = error

    @classmethod
    def ok(cls, value: T) -> Result[T]:
        """Create successful result."""

        return cls(value=value)

    @classmethod
    def error(cls, error: str) -> Result[T]:
        """Create error result."""

        return cls(error=error)

    def is_ok(self) -> bool:
        """Check if result is successful."""

        return self._error is None

    def is_error(self) -> bool:
        """Check if result is an error."""

        return self._error is not None

    def unwrap(self) -> T:
        """Get value or raise if error."""

        if self._error:
            raise ValueError(f"Cannot unwrap error result: {self._error}")
        if self._value is None:
            raise ValueError("Cannot unwrap None value")
        return self._value

    def unwrap_or(self, default: T) -> T:
        """Get value or return default if error."""

        return self._value if self._error is None and self._value is not None else default

    def error_message(self) -> str | None:
        """Get error message if error result."""

        return self._error
