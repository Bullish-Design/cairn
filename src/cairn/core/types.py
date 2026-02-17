"""Type definitions for Cairn operations.

This module provides TypedDict definitions and type aliases for improved
type safety throughout the Cairn codebase.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Generic, Protocol, TypeAlias, TypedDict, TypeVar

from fsdantic import Workspace


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


ExternalFunctionResult: TypeAlias = str | bool | list[str] | list[SearchContentMatchData] | dict[str, Any]

ExternalFunction: TypeAlias = Callable[..., Awaitable[ExternalFunctionResult]]

ReadFileFunction: TypeAlias = Callable[[str], Awaitable[str]]
WriteFileFunction: TypeAlias = Callable[[str, str], Awaitable[bool]]
FileExistsFunction: TypeAlias = Callable[[str], Awaitable[bool]]
SearchFilesFunction: TypeAlias = Callable[[str], Awaitable[list[str]]]
SubmitResultFunction: TypeAlias = Callable[[str, list[str]], Awaitable[bool]]
LogFunction: TypeAlias = Callable[[str], Awaitable[bool]]


class ListDirFunction(Protocol):
    """Protocol for list_dir with default path."""

    def __call__(self, path: str = ".") -> Awaitable[list[str]]: ...


class SearchContentFunction(Protocol):
    """Protocol for search_content with optional path."""

    def __call__(self, pattern: str, path: str = ".") -> Awaitable[list[SearchContentMatchData]]: ...


class ExternalTools(TypedDict):
    """Typed map of external functions exposed to agents."""

    read_file: ReadFileFunction
    write_file: WriteFileFunction
    list_dir: ListDirFunction
    file_exists: FileExistsFunction
    search_files: SearchFilesFunction
    search_content: SearchContentFunction
    submit_result: SubmitResultFunction
    log: LogFunction


ToolsFactory: TypeAlias = Callable[[str, Workspace, Workspace], ExternalTools]

ExecutionResult: TypeAlias = dict[str, Any]


class FileEntryProtocol(Protocol):
    """Protocol for file entries returned from workspace queries."""

    path: str
    content: str | bytes | None


class GrailCheckResult(Protocol):
    """Protocol for Grail check results."""

    valid: bool
    errors: list[object] | None


class GrailScript(Protocol):
    """Protocol for Grail script objects."""

    def check(self) -> GrailCheckResult:
        """Validate the script before execution."""
        ...

    async def run(self, inputs: dict[str, Any], externals: ExternalTools) -> dict[str, Any]:
        """Run the Grail script."""
        ...


T = TypeVar("T")


class Result(Generic[T]):
    """Generic result wrapper for operations that may fail."""

    def __init__(self, value: T | None = None, error: str | None = None) -> None:
        self._value = value
        self._error = error

    @classmethod
    def ok(cls, value: T) -> "Result[T]":
        """Create successful result."""

        return cls(value=value)

    @classmethod
    def error(cls, error: str) -> "Result[T]":
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
