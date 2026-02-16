# Refactoring Step 1: Foundation - Error Infrastructure & Constants

## Overview
This step establishes the foundational infrastructure for improved error handling and code maintainability. It creates a proper error hierarchy, replaces magic numbers with named constants, and adds missing module docstrings. This step is critical as it sets up the foundation that later refactoring steps will build upon.

**Priority:** 🔴 CRITICAL (Foundation for other steps)
**Estimated Effort:** 4-6 hours
**Dependencies:** None - this is the foundation step

---

## Issues Addressed

### Issue #11: Improve Error Hierarchy
**Current State:**
- Only one custom exception type (`CodeProviderError`)
- No distinction between recoverable and fatal errors
- No error codes for programmatic handling

**Target State:**
- Comprehensive error hierarchy with base `CairnError`
- Clear categorization of error types
- Error codes for programmatic handling

### Issue #8: Replace Magic Numbers with Constants
**Locations:**
- `orchestrator.py:439` - `86400 * 7` (week in seconds)
- `signals.py:45` - `0.5` (poll interval)
- `external_models.py:15` - `10 * 1024 * 1024` (max file size)

### Missing Module Docstrings
**Files lacking docstrings:**
- `commands.py` - No module docstring
- `typer_cli.py` - No module docstring
- Other modules may have incomplete docstrings

---

## Detailed Implementation Steps

### 1. Create Error Hierarchy Module

**File:** `cairn/exceptions.py` (NEW FILE)

Create a new exceptions module with the following structure:

```python
"""Exception hierarchy for Cairn operations.

This module defines the complete exception hierarchy for Cairn, providing
structured error handling with error codes for programmatic handling.
"""

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
    ):
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
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base} ({context_str})"
        return base


class RecoverableError(CairnError):
    """Errors that can be retried with potential for success.

    These errors indicate transient failures that may succeed on retry,
    such as network timeouts, temporary file locks, or resource unavailability.
    """
    pass


class FatalError(CairnError):
    """Errors that cannot be recovered through retry.

    These errors indicate permanent failures that require intervention,
    such as configuration errors, invalid input, or system constraints.
    """
    pass


class AgentError(CairnError):
    """Errors related to agent lifecycle and execution."""
    pass


class AgentStateError(AgentError):
    """Invalid agent state transition attempted."""
    pass


class AgentExecutionError(AgentError):
    """Error during agent code generation or execution."""
    pass


class ValidationError(FatalError):
    """Input validation failures."""
    pass


class PathValidationError(ValidationError):
    """Path validation failure (traversal, absolute path, etc.)."""
    pass


class ResourceError(CairnError):
    """Resource exhaustion or limit errors."""
    pass


class ResourceLimitError(ResourceError):
    """Resource limit exceeded (memory, time, disk space)."""
    pass


class WorkspaceError(RecoverableError):
    """Errors related to workspace operations."""
    pass


class WorkspaceMergeError(WorkspaceError):
    """Workspace merge operation failed."""
    pass


class LifecycleError(CairnError):
    """Errors related to lifecycle record persistence."""
    pass


class VersionConflictError(LifecycleError, RecoverableError):
    """Optimistic locking version conflict - can be retried."""
    pass


class ProviderError(CairnError):
    """Base class for code provider errors."""
    pass


class CodeProviderError(ProviderError):
    """Legacy exception - kept for backward compatibility."""
    pass


class PluginError(FatalError):
    """Plugin loading or execution errors."""
    pass


class ConfigurationError(FatalError):
    """Configuration or settings errors."""
    pass


class SecurityError(FatalError):
    """Security-related errors (secrets, sandbox violations)."""
    pass


class SecretsDetectedError(SecurityError):
    """Secrets detected in agent submission."""
    pass


class TimeoutError(ResourceLimitError, RecoverableError):
    """Operation exceeded time limit."""
    pass
```

### 2. Create Constants Module

**File:** `cairn/constants.py` (NEW FILE)

```python
"""Constants for Cairn configuration and limits.

This module defines all magic numbers and configuration constants used
throughout the Cairn codebase, providing a single source of truth.
"""

# Time constants (in seconds)
SECOND = 1.0
MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0
WEEK = 604800.0  # 7 * DAY

# Polling intervals
SIGNAL_POLL_INTERVAL_SECONDS = 0.5
WATCHER_DEBOUNCE_SECONDS = 0.1

# File size limits
KB = 1024
MB = 1024 * KB
GB = 1024 * MB

MAX_FILE_SIZE_BYTES = 10 * MB
MAX_CONTENT_SIZE_BYTES = 10 * MB

# Execution limits
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_MEMORY_BYTES = 100 * MB

# Lifecycle management
LIFECYCLE_CLEANUP_MAX_AGE_SECONDS = 7 * DAY  # 1 week
LIFECYCLE_MAX_RETRY_ATTEMPTS = 3
LIFECYCLE_RETRY_INITIAL_DELAY_SECONDS = 0.1
LIFECYCLE_RETRY_BACKOFF_FACTOR = 2.0

# Queue configuration
DEFAULT_QUEUE_PRIORITY = 5
MIN_QUEUE_PRIORITY = 1
MAX_QUEUE_PRIORITY = 10

# Concurrency limits
DEFAULT_MAX_CONCURRENT_AGENTS = 4
MAX_WORKSPACE_CACHE_SIZE = 100

# Retry configuration
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_INITIAL_DELAY_SECONDS = 1.0
DEFAULT_RETRY_BACKOFF_FACTOR = 2.0
DEFAULT_RETRY_MAX_DELAY_SECONDS = 60.0

# Regex timeout (for ReDoS protection)
REGEX_TIMEOUT_SECONDS = 1.0

# Agent ID constraints
AGENT_ID_MAX_LENGTH = 255
AGENT_ID_ALLOWED_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
```

### 3. Update Existing Code to Use New Exceptions

**File:** `cairn/orchestrator.py`

Replace:
```python
# Line ~412-416 (broad exception catching)
except Exception as exc:
    if ctx is not None:
        ctx.error = str(exc)
```

With:
```python
from cairn.exceptions import (
    AgentExecutionError,
    RecoverableError,
    FatalError,
    CairnError,
)

# More specific exception handling
except CairnError as exc:
    if ctx is not None:
        ctx.error = str(exc)
        logger.error(
            "Agent execution failed",
            extra={"agent_id": agent_id, "error_code": exc.error_code, "context": exc.context}
        )
    if isinstance(exc, RecoverableError):
        # Could be retried in future
        pass
    else:
        # Fatal error, cannot recover
        pass
except Exception as exc:
    # Unexpected error - wrap in CairnError
    if ctx is not None:
        ctx.error = str(exc)
        logger.exception("Unexpected error in agent execution", extra={"agent_id": agent_id})
    # Re-raise unexpected errors for debugging
    raise
```

### 4. Replace Magic Numbers with Constants

**File:** `cairn/orchestrator.py`

```python
from cairn.constants import LIFECYCLE_CLEANUP_MAX_AGE_SECONDS

# Line ~439 - Replace magic number
# OLD:
max_age_seconds: float = 86400 * 7

# NEW:
max_age_seconds: float = LIFECYCLE_CLEANUP_MAX_AGE_SECONDS
```

**File:** `cairn/signals.py`

```python
from cairn.constants import SIGNAL_POLL_INTERVAL_SECONDS

# Line ~45 - Replace magic number
# OLD:
await asyncio.sleep(0.5)

# NEW:
await asyncio.sleep(SIGNAL_POLL_INTERVAL_SECONDS)
```

**File:** `cairn/external_models.py`

```python
from cairn.constants import MAX_FILE_SIZE_BYTES

# Line ~15 - Replace magic number
# OLD:
max_length=10 * 1024 * 1024

# NEW:
max_length=MAX_FILE_SIZE_BYTES
```

### 5. Add Module Docstrings

**File:** `cairn/commands.py`

Add at the top:
```python
"""Command pattern implementation for Cairn orchestrator operations.

This module defines the command objects used to communicate with the orchestrator,
implementing a command pattern for operation dispatching. Commands are immutable
dataclasses that encapsulate operation parameters.

Supported Commands:
    - QueueCommand: Queue a new agent task
    - AcceptCommand: Accept an agent's changes
    - RejectCommand: Reject an agent's changes
    - ListCommand: List agents with filtering
    - InspectCommand: Inspect agent details
    - StatusCommand: Get orchestrator status
"""
```

**File:** `cairn/typer_cli.py`

Add at the top:
```python
"""Typer-based CLI interface for Cairn orchestrator.

This module provides the command-line interface using the Typer library,
offering commands for managing agent tasks, inspecting state, and controlling
the orchestrator lifecycle.

The CLI communicates with the orchestrator through the command pattern defined
in commands.py, providing a user-friendly interface for all orchestrator operations.
"""
```

### 6. Update Error Raising Throughout Codebase

**File:** `cairn/external_models.py`

```python
from cairn.exceptions import PathValidationError

# Line ~18-28 - Update path validation
def _validate_path(value: str, *, allow_root: bool = False) -> str:
    """Validate a path for sandbox-safe use."""
    path = Path(value)
    if path.is_absolute():
        raise PathValidationError(
            f"Absolute paths not allowed in sandbox: {value}",
            error_code="PATH_ABSOLUTE",
            context={"path": value}
        )
    if ".." in path.parts:
        raise PathValidationError(
            f"Path traversal not allowed: {value}",
            error_code="PATH_TRAVERSAL",
            context={"path": value}
        )
    return value
```

---

## Testing Requirements

### Unit Tests to Add/Update

**File:** `tests/test_exceptions.py` (NEW FILE)

```python
"""Tests for exception hierarchy and error handling."""

import pytest
from cairn.exceptions import (
    CairnError,
    RecoverableError,
    FatalError,
    ValidationError,
    PathValidationError,
    VersionConflictError,
)


def test_cairn_error_basic():
    """Test basic CairnError creation and formatting."""
    error = CairnError("Test error")
    assert str(error) == "[CAIRNERROR] Test error"
    assert error.error_code == "CAIRNERROR"
    assert error.message == "Test error"
    assert error.context == {}


def test_cairn_error_with_code():
    """Test CairnError with custom error code."""
    error = CairnError("Test error", error_code="CUSTOM_001")
    assert error.error_code == "CUSTOM_001"
    assert "CUSTOM_001" in str(error)


def test_cairn_error_with_context():
    """Test CairnError with context information."""
    error = CairnError(
        "Test error",
        error_code="TEST_ERROR",
        context={"agent_id": "test-123", "attempt": 2}
    )
    error_str = str(error)
    assert "TEST_ERROR" in error_str
    assert "agent_id=test-123" in error_str
    assert "attempt=2" in error_str


def test_recoverable_error_hierarchy():
    """Test RecoverableError is a CairnError."""
    error = RecoverableError("Transient failure")
    assert isinstance(error, CairnError)
    assert isinstance(error, RecoverableError)


def test_fatal_error_hierarchy():
    """Test FatalError is a CairnError."""
    error = FatalError("Permanent failure")
    assert isinstance(error, CairnError)
    assert isinstance(error, FatalError)


def test_path_validation_error():
    """Test PathValidationError creation."""
    error = PathValidationError(
        "Invalid path",
        error_code="PATH_TRAVERSAL",
        context={"path": "../etc/passwd"}
    )
    assert isinstance(error, ValidationError)
    assert isinstance(error, FatalError)
    assert error.error_code == "PATH_TRAVERSAL"


def test_version_conflict_is_recoverable():
    """Test VersionConflictError is recoverable."""
    error = VersionConflictError("Version mismatch")
    assert isinstance(error, RecoverableError)
    assert isinstance(error, LifecycleError)
```

**File:** `tests/test_constants.py` (NEW FILE)

```python
"""Tests for constants module."""

import pytest
from cairn.constants import (
    SECOND,
    MINUTE,
    HOUR,
    DAY,
    WEEK,
    KB,
    MB,
    GB,
    MAX_FILE_SIZE_BYTES,
    LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
)


def test_time_constants():
    """Test time constants are correct."""
    assert SECOND == 1.0
    assert MINUTE == 60.0
    assert HOUR == 3600.0
    assert DAY == 86400.0
    assert WEEK == 604800.0


def test_size_constants():
    """Test size constants are correct."""
    assert KB == 1024
    assert MB == 1024 * 1024
    assert GB == 1024 * 1024 * 1024


def test_max_file_size():
    """Test max file size is reasonable."""
    assert MAX_FILE_SIZE_BYTES == 10 * MB
    assert MAX_FILE_SIZE_BYTES == 10485760


def test_lifecycle_cleanup_age():
    """Test lifecycle cleanup age matches week."""
    assert LIFECYCLE_CLEANUP_MAX_AGE_SECONDS == WEEK
    assert LIFECYCLE_CLEANUP_MAX_AGE_SECONDS == 604800.0
```

### Integration Test Scenarios

None for this step - this is foundational infrastructure that doesn't change behavior.

### Manual Testing Checklist

- [ ] All existing tests pass after changes
- [ ] Import `cairn.exceptions` in Python REPL - no errors
- [ ] Import `cairn.constants` in Python REPL - no errors
- [ ] Run `ruff check cairn/` - no new errors
- [ ] Run `mypy cairn/` - no new type errors
- [ ] Verify module docstrings appear in `help()` output

---

## Files to Create

1. `cairn/exceptions.py` - Complete error hierarchy
2. `cairn/constants.py` - All magic numbers as constants
3. `tests/test_exceptions.py` - Exception tests
4. `tests/test_constants.py` - Constants tests

---

## Files to Modify

1. `cairn/orchestrator.py`
   - Import and use new exceptions
   - Replace magic numbers with constants
   - Update exception handling to be more specific

2. `cairn/signals.py`
   - Import constants
   - Replace poll interval magic number

3. `cairn/external_models.py`
   - Import new exceptions
   - Replace max file size magic number
   - Update path validation to use new exceptions

4. `cairn/commands.py`
   - Add module docstring

5. `cairn/typer_cli.py`
   - Add module docstring

6. `cairn/providers.py`
   - Update `CodeProviderError` to inherit from new hierarchy (if needed)

7. Any other files with magic numbers (search for literal numbers)

---

## Validation Criteria

### Success Criteria
- ✅ All new exception classes defined and tested
- ✅ All constants defined in single module
- ✅ All magic numbers replaced with named constants
- ✅ All module docstrings added
- ✅ All existing tests pass
- ✅ No ruff or mypy errors introduced
- ✅ Code coverage maintained or improved

### Breaking Changes
- None - this step is backward compatible
- Old `CodeProviderError` still works (inherits from new hierarchy)
- Existing code continues to work unchanged

### Rollback Plan
If issues arise:
1. Revert new files: `git rm cairn/exceptions.py cairn/constants.py`
2. Revert modified files: `git checkout cairn/orchestrator.py cairn/signals.py cairn/external_models.py`
3. Revert test files: `git rm tests/test_exceptions.py tests/test_constants.py`

---

## Dependencies for Next Steps

This step is a **prerequisite** for:
- **Step 4:** Retry Logic Integration (uses `RecoverableError`)
- **Step 5:** Security Hardening (uses `SecurityError`, `TimeoutError`)
- **Step 6:** Concurrency improvements (uses `VersionConflictError`)

All later steps will benefit from:
- Structured error handling with error codes
- Named constants for configuration
- Better error context for debugging

---

## Notes for Implementer

### Key Design Decisions

1. **Error Hierarchy Design:**
   - `RecoverableError` vs `FatalError` distinction guides retry logic
   - `VersionConflictError` is both `LifecycleError` and `RecoverableError` (multiple inheritance)
   - Each error has an error code for programmatic handling

2. **Constants Organization:**
   - All time constants derived from `SECOND`
   - All size constants derived from `KB`
   - Grouped by category (time, size, limits, retry config)

3. **Backward Compatibility:**
   - Keep `CodeProviderError` as alias to new `ProviderError`
   - Existing code using `RuntimeError` base class still works
   - No API changes - only internal improvements

### Common Pitfalls to Avoid

1. **Don't remove old exceptions** - Keep `CodeProviderError` for compatibility
2. **Don't change exception behavior** - Only add structure, don't change semantics
3. **Test thoroughly** - Ensure all exception paths still work
4. **Update imports carefully** - Add new imports without breaking existing ones

### Time Estimates

- Create exceptions.py: 1.5 hours
- Create constants.py: 0.5 hours
- Update orchestrator.py: 1 hour
- Update other files: 1 hour
- Write tests: 1.5 hours
- Testing and validation: 1 hour
- **Total: 6.5 hours**

---

## Questions for Product Owner

1. Should we keep `CodeProviderError` indefinitely or deprecate it?
2. Are there any other magic numbers we should extract?
3. Do we want error codes to follow a specific pattern (e.g., `ERR_001`)?
4. Should we add structured logging in this step or defer to later?

---

## References

- CODE_REVIEW.md - Section 4.1 (Exception Hierarchy)
- CODE_REVIEW.md - Section 2.4 (Magic Numbers)
- CODE_REVIEW.md - Section 2.4 (Missing Module Docstrings)
- orchestrator.py:412-416 (Broad exception catching)
- orchestrator.py:439 (Magic number: 86400 * 7)
- signals.py:45 (Magic number: 0.5)
- external_models.py:15 (Magic number: 10 * 1024 * 1024)
