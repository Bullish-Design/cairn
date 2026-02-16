# Refactoring Step 2: Type Safety Improvements

## Overview
This step improves type safety throughout the codebase by reducing the use of `Any` types, adding more specific type hints, and introducing TypedDict for structured data. This enhances code maintainability, enables better IDE support, and catches potential bugs at type-checking time.

**Priority:** 🟡 HIGH
**Estimated Effort:** 3-4 hours
**Dependencies:** Step 1 (uses constants from Step 1)

---

## Issues Addressed

### Issue #10: Type Any Overuse
**Locations:**
- `external_functions.py:7, 26, 65` - `ExternalFunction = Callable[..., Awaitable[Any]]`
- `external_functions.py:135-157` - Missing return type annotations
- `orchestrator.py` - Various `Any` usages in tool factories

**Current State:**
- Too permissive `Any` usage reduces type safety
- External function wrappers lack specific return types
- Tool dictionaries use `dict[str, Callable[..., Any]]`

**Target State:**
- Specific return types where possible
- TypedDict for structured data
- Generic types for flexible but type-safe code

---

## Detailed Implementation Steps

### 1. Define TypedDict for Structured Data

**File:** `cairn/types.py` (NEW FILE)

```python
"""Type definitions for Cairn operations.

This module provides TypedDict definitions and type aliases for improved
type safety throughout the Cairn codebase.
"""

from typing import Any, Awaitable, Callable, Protocol, TypeAlias, TypedDict
from pathlib import Path


# External function response types
class FileListResponse(TypedDict):
    """Response from list_files external function."""
    files: list[str]


class FileContentResponse(TypedDict):
    """Response from read_file external function."""
    content: str
    path: str


class FileWriteResponse(TypedDict):
    """Response from write_file external function."""
    success: bool
    path: str


class SearchResult(TypedDict):
    """Single search result from search_content."""
    file: str
    line_number: int
    line_content: str
    match: str


class SearchResponse(TypedDict):
    """Response from search_content external function."""
    results: list[SearchResult]
    total_matches: int


class CommandResult(TypedDict):
    """Result from command execution."""
    stdout: str
    stderr: str
    exit_code: int


# External function types
ExternalFunctionResult: TypeAlias = (
    FileListResponse
    | FileContentResponse
    | FileWriteResponse
    | SearchResponse
    | CommandResult
    | dict[str, Any]  # Fallback for custom functions
)

ExternalFunction: TypeAlias = Callable[..., Awaitable[ExternalFunctionResult]]

# Tool factory type
ToolsFactory: TypeAlias = Callable[
    [str, Any, Any],  # agent_id, agent_workspace, stable_workspace
    dict[str, ExternalFunction]
]


# Agent context data
class AgentMetadata(TypedDict, total=False):
    """Metadata for agent execution context."""
    created_at: float
    updated_at: float
    priority: int
    tags: list[str]
    custom_fields: dict[str, Any]


# Lifecycle record data
class LifecycleSnapshot(TypedDict):
    """Snapshot of agent state for lifecycle tracking."""
    agent_id: str
    state: str
    timestamp: float
    error: str | None
    submission_path: str | None


# Provider configuration
class ProviderConfig(TypedDict, total=False):
    """Configuration for code providers."""
    provider_type: str
    source: str | Path
    kwargs: dict[str, Any]


# Workspace merge result
class MergeResult(TypedDict):
    """Result of workspace merge operation."""
    success: bool
    conflicts: list[str]
    merged_files: list[str]
    error: str | None


# Queue statistics
class QueueStats(TypedDict):
    """Statistics about the task queue."""
    size: int
    pending_count: int
    active_count: int
    oldest_task_age: float | None


# Orchestrator status
class OrchestratorStatus(TypedDict):
    """Status information for the orchestrator."""
    running: bool
    active_agents: int
    queued_tasks: int
    max_concurrent: int
    total_processed: int
    uptime_seconds: float


class Protocol(Protocol):
    """Base protocol for runtime protocols."""
    pass


class WorkspaceProtocol(Protocol):
    """Protocol for workspace objects."""

    async def read_file(self, path: str) -> str:
        """Read file from workspace."""
        ...

    async def write_file(self, path: str, content: str) -> None:
        """Write file to workspace."""
        ...

    async def list_files(self, pattern: str = "*") -> list[str]:
        """List files in workspace."""
        ...

    async def close(self) -> None:
        """Close workspace connection."""
        ...
```

### 2. Update External Functions Type Annotations

**File:** `cairn/external_functions.py`

```python
from typing import Any, Awaitable, Callable
from cairn.types import (
    ExternalFunction,
    ExternalFunctionResult,
    FileListResponse,
    FileContentResponse,
    FileWriteResponse,
    SearchResponse,
    SearchResult,
)

# OLD:
ExternalFunction = Callable[..., Awaitable[Any]]

# NEW: Remove this line, use imported type instead

# Update function signatures:

async def list_files_impl(
    request: ListFilesRequest,
    agent_ws: Workspace,
    stable_ws: Workspace,
) -> FileListResponse:
    """List files in agent workspace.

    Returns:
        FileListResponse with list of file paths
    """
    files = await agent_ws.list_files(request.pattern)
    return FileListResponse(files=files)


async def read_file_impl(
    request: ReadFileRequest,
    agent_ws: Workspace,
    stable_ws: Workspace,
) -> FileContentResponse:
    """Read file content from workspace.

    Returns:
        FileContentResponse with content and path
    """
    content = await agent_ws.read_file(request.path)
    return FileContentResponse(content=content, path=request.path)


async def write_file_impl(
    request: WriteFileRequest,
    agent_ws: Workspace,
    stable_ws: Workspace,
) -> FileWriteResponse:
    """Write file to agent workspace.

    Returns:
        FileWriteResponse with success status and path
    """
    await agent_ws.write_file(request.path, request.content)
    return FileWriteResponse(success=True, path=request.path)


async def search_content_impl(
    request: SearchContentRequest,
    agent_ws: Workspace,
    stable_ws: Workspace,
) -> SearchResponse:
    """Search file contents using regex pattern.

    Returns:
        SearchResponse with list of search results
    """
    import re

    results: list[SearchResult] = []
    files = await agent_ws.list_files(request.file_pattern or "*")
    pattern = re.compile(request.pattern)

    for file_path in files:
        try:
            content = await agent_ws.read_file(file_path)
            for line_num, line in enumerate(content.splitlines(), 1):
                if match := pattern.search(line):
                    results.append(
                        SearchResult(
                            file=file_path,
                            line_number=line_num,
                            line_content=line.strip(),
                            match=match.group(0),
                        )
                    )
        except Exception:
            continue  # Skip files that can't be read

    return SearchResponse(results=results, total_matches=len(results))


# Update wrapper function:

def create_external_functions(
    agent_id: str,
    agent_ws: Workspace,
    stable_ws: Workspace,
) -> dict[str, ExternalFunction]:
    """Create external functions for agent use.

    Args:
        agent_id: Unique agent identifier
        agent_ws: Agent's workspace
        stable_ws: Stable workspace for reading

    Returns:
        Dictionary mapping function names to callable functions
    """
    # Implementation...
    return {
        "list_files": lambda req: list_files_impl(req, agent_ws, stable_ws),
        "read_file": lambda req: read_file_impl(req, agent_ws, stable_ws),
        "write_file": lambda req: write_file_impl(req, agent_ws, stable_ws),
        "search_content": lambda req: search_content_impl(req, agent_ws, stable_ws),
    }
```

### 3. Update Orchestrator Type Hints

**File:** `cairn/orchestrator.py`

```python
from typing import Any, Callable
from cairn.types import ToolsFactory, OrchestratorStatus, QueueStats
from cairn.workspace import Workspace  # Assume this exists or use Protocol

# Update __init__ signature:

def __init__(
    self,
    project_root: Path | str = ".",
    cairn_home: Path | str | None = None,
    config: OrchestratorSettings | None = None,
    executor_settings: ExecutorSettings | None = None,
    code_provider: CodeProvider | None = None,
    tools_factory: ToolsFactory | None = None,  # More specific type
):
    """Initialize Cairn orchestrator.

    Args:
        project_root: Root directory of the project
        cairn_home: Cairn home directory (default: ~/.cairn)
        config: Orchestrator configuration
        executor_settings: Execution limits and settings
        code_provider: Provider for agent code
        tools_factory: Factory function for creating external tools
    """
    # Implementation...


# Add return type annotations to methods:

async def get_status(self) -> OrchestratorStatus:
    """Get current orchestrator status.

    Returns:
        OrchestratorStatus with current state information
    """
    return OrchestratorStatus(
        running=self._running,
        active_agents=len(self.active_agents),
        queued_tasks=self.queue.size(),
        max_concurrent=self.config.max_concurrent_agents,
        total_processed=self._total_processed,
        uptime_seconds=time.time() - self._start_time,
    )


async def get_queue_stats(self) -> QueueStats:
    """Get queue statistics.

    Returns:
        QueueStats with queue information
    """
    tasks = self.queue.list_all()
    oldest = min((t.created_at for t in tasks), default=None)
    oldest_age = time.time() - oldest if oldest else None

    return QueueStats(
        size=self.queue.size(),
        pending_count=len([t for t in tasks if t.status == "pending"]),
        active_count=len(self.active_agents),
        oldest_task_age=oldest_age,
    )
```

### 4. Add Type Stubs for External Dependencies

If external dependencies lack type information, create stub files:

**File:** `cairn/py.typed` (NEW FILE)

Empty marker file to indicate this package supports typing.

**File:** `stubs/grail/__init__.pyi` (NEW FILE, if needed)

```python
"""Type stubs for grail module."""

from typing import Any, Protocol


class GrailScript(Protocol):
    """Protocol for Grail script objects."""

    async def run(
        self,
        inputs: dict[str, Any],
        externals: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the Grail script."""
        ...


class GrailExecutionError(Exception):
    """Grail script execution error."""
    pass


class InputError(Exception):
    """Grail input validation error."""
    pass


def load(script_path: str) -> GrailScript:
    """Load a Grail script (legacy API)."""
    ...
```

### 5. Add Generic Types for Flexibility

**File:** `cairn/types.py` (additions)

```python
from typing import Generic, TypeVar

T = TypeVar("T")
R = TypeVar("R")


class Result(Generic[T]):
    """Generic result wrapper for operations that may fail.

    Usage:
        result = Result.ok(value)
        result = Result.error("Something went wrong")

        if result.is_ok():
            value = result.unwrap()
    """

    def __init__(self, value: T | None = None, error: str | None = None):
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
```

---

## Testing Requirements

### Unit Tests to Add/Update

**File:** `tests/test_types.py` (NEW FILE)

```python
"""Tests for type definitions and type safety."""

import pytest
from typing import get_type_hints
from cairn.types import (
    FileListResponse,
    FileContentResponse,
    SearchResponse,
    SearchResult,
    Result,
)
from cairn.external_functions import (
    list_files_impl,
    read_file_impl,
    search_content_impl,
)


def test_file_list_response_structure():
    """Test FileListResponse TypedDict structure."""
    response: FileListResponse = {"files": ["a.py", "b.py"]}
    assert isinstance(response["files"], list)
    assert all(isinstance(f, str) for f in response["files"])


def test_file_content_response_structure():
    """Test FileContentResponse TypedDict structure."""
    response: FileContentResponse = {
        "content": "test content",
        "path": "test.py"
    }
    assert isinstance(response["content"], str)
    assert isinstance(response["path"], str)


def test_search_result_structure():
    """Test SearchResult TypedDict structure."""
    result: SearchResult = {
        "file": "test.py",
        "line_number": 42,
        "line_content": "def foo():",
        "match": "foo"
    }
    assert isinstance(result["file"], str)
    assert isinstance(result["line_number"], int)
    assert isinstance(result["line_content"], str)
    assert isinstance(result["match"], str)


def test_search_response_structure():
    """Test SearchResponse TypedDict structure."""
    response: SearchResponse = {
        "results": [],
        "total_matches": 0
    }
    assert isinstance(response["results"], list)
    assert isinstance(response["total_matches"], int)


def test_result_ok():
    """Test Result.ok() creates successful result."""
    result = Result.ok(42)
    assert result.is_ok()
    assert not result.is_error()
    assert result.unwrap() == 42


def test_result_error():
    """Test Result.error() creates error result."""
    result = Result.error("Something failed")
    assert result.is_error()
    assert not result.is_ok()
    assert result.error_message() == "Something failed"


def test_result_unwrap_error_raises():
    """Test unwrapping error result raises."""
    result = Result.error("Failed")
    with pytest.raises(ValueError, match="Cannot unwrap error result"):
        result.unwrap()


def test_result_unwrap_or():
    """Test unwrap_or returns default on error."""
    result = Result.error("Failed")
    assert result.unwrap_or(999) == 999

    result_ok = Result.ok(42)
    assert result_ok.unwrap_or(999) == 42


def test_external_function_return_types():
    """Test external functions have correct return type hints."""
    # This test verifies type hints are present
    hints = get_type_hints(list_files_impl)
    assert "return" in hints

    hints = get_type_hints(read_file_impl)
    assert "return" in hints

    hints = get_type_hints(search_content_impl)
    assert "return" in hints
```

### Type Checking Tests

**File:** `tests/test_type_checking.py` (NEW FILE)

```python
"""Tests that verify type checking works correctly."""

import pytest
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # These should type-check correctly
    from cairn.types import FileListResponse, ExternalFunction

    # Test valid assignments
    response: FileListResponse = {"files": ["a.py"]}

    # Test that invalid assignments would fail type checking
    # (These are commented out but would fail mypy)
    # invalid: FileListResponse = {"files": [1, 2, 3]}  # Type error
    # invalid2: FileListResponse = {"wrong_key": []}     # Type error


def test_type_annotations_present():
    """Test that key functions have type annotations."""
    from cairn.external_functions import create_external_functions
    from cairn.orchestrator import CairnOrchestrator

    # Check function has annotations
    assert hasattr(create_external_functions, "__annotations__")
    assert "return" in create_external_functions.__annotations__

    # Check class methods have annotations
    assert hasattr(CairnOrchestrator.get_status, "__annotations__")
```

### Manual Type Checking

Run mypy to verify type safety:

```bash
# Should pass with no errors
mypy cairn/ --strict

# Check specific files
mypy cairn/external_functions.py --strict
mypy cairn/orchestrator.py --strict
mypy cairn/types.py --strict
```

---

## Files to Create

1. `cairn/types.py` - Type definitions and TypedDict classes
2. `cairn/py.typed` - Marker file for typing support
3. `tests/test_types.py` - Type definition tests
4. `tests/test_type_checking.py` - Type checking validation tests
5. `stubs/grail/__init__.pyi` - Type stubs for grail (if needed)

---

## Files to Modify

1. `cairn/external_functions.py`
   - Replace generic `Any` with specific TypedDict returns
   - Add return type annotations to all functions
   - Update `ExternalFunction` type alias

2. `cairn/orchestrator.py`
   - Update `tools_factory` parameter type
   - Add return type annotations to methods
   - Use `ToolsFactory` type alias

3. `cairn/agent.py`
   - Add type hints for context fields if missing

4. `pyproject.toml`
   - Add `py.typed` to package data
   - Ensure mypy configuration is present

---

## Validation Criteria

### Success Criteria
- ✅ All TypedDict definitions created and used
- ✅ Return type annotations added to all public functions
- ✅ `Any` usage reduced by at least 50%
- ✅ `mypy --strict` passes with no errors in modified files
- ✅ All existing tests pass
- ✅ New type tests pass
- ✅ IDE autocomplete works for TypedDict fields

### Breaking Changes
- None - this is purely additive
- All changes are type annotations only
- Runtime behavior unchanged

### Rollback Plan
If issues arise:
1. Revert new files: `git rm cairn/types.py cairn/py.typed`
2. Revert type annotations in modified files
3. Revert test files

---

## Dependencies for Next Steps

This step enables:
- **All later steps:** Better IDE support and type safety
- **Step 8:** Testing infrastructure (type-safe test fixtures)

Benefits all code going forward:
- Catch bugs at type-check time
- Better IDE autocomplete
- Self-documenting code through types

---

## Notes for Implementer

### Key Design Decisions

1. **TypedDict vs dataclass:**
   - Use TypedDict for dict-like data (JSON-serializable)
   - Use dataclass for objects with methods
   - TypedDict is better for external function returns (JSON)

2. **Protocol vs Abstract Base Class:**
   - Use Protocol for structural typing (duck typing)
   - Use ABC for explicit inheritance hierarchies
   - Workspace uses Protocol for flexibility

3. **Generic Result type:**
   - Optional - provides Rust-like error handling
   - Can be used in future refactoring
   - Not required immediately

### Common Pitfalls to Avoid

1. **Don't break runtime behavior** - Type annotations are metadata only
2. **Don't make types too specific** - Leave room for flexibility
3. **Don't add typing dependencies** - Use stdlib typing only
4. **Test with mypy** - Ensure types actually work

### Time Estimates

- Create types.py: 1.5 hours
- Update external_functions.py: 1 hour
- Update orchestrator.py: 0.5 hours
- Add type stubs: 0.5 hours
- Write tests: 1 hour
- Run mypy and fix issues: 1 hour
- **Total: 5.5 hours**

---

## Questions for Product Owner

1. Should we require `mypy --strict` to pass in CI?
2. Do we want to add type checking to pre-commit hooks?
3. Should we use Protocol extensively or stick with concrete types?
4. Do we need type stubs for all external dependencies?

---

## References

- CODE_REVIEW.md - Section 2.1 (Type Safety)
- CODE_REVIEW.md - Issue #10 (Type Any Overuse)
- external_functions.py:7, 26, 65 (Any usage)
- external_functions.py:135-157 (Missing return types)
- orchestrator.py (tools_factory parameter)
- PEP 589 (TypedDict)
- PEP 544 (Protocols)
