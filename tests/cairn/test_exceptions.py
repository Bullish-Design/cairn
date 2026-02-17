"""Tests for exception hierarchy and error handling."""

from cairn.core.exceptions import (
    CairnError,
    FatalError,
    LifecycleError,
    PathValidationError,
    RecoverableError,
    ValidationError,
    VersionConflictError,
)


def test_cairn_error_basic() -> None:
    """Test basic CairnError creation and formatting."""
    error = CairnError("Test error")
    assert str(error) == "[CAIRNERROR] Test error"
    assert error.error_code == "CAIRNERROR"
    assert error.message == "Test error"
    assert error.context == {}


def test_cairn_error_with_code() -> None:
    """Test CairnError with custom error code."""
    error = CairnError("Test error", error_code="CUSTOM_001")
    assert error.error_code == "CUSTOM_001"
    assert "CUSTOM_001" in str(error)


def test_cairn_error_with_context() -> None:
    """Test CairnError with context information."""
    error = CairnError(
        "Test error",
        error_code="TEST_ERROR",
        context={"agent_id": "test-123", "attempt": 2},
    )
    error_str = str(error)
    assert "TEST_ERROR" in error_str
    assert "agent_id=test-123" in error_str
    assert "attempt=2" in error_str


def test_recoverable_error_hierarchy() -> None:
    """Test RecoverableError is a CairnError."""
    error = RecoverableError("Transient failure")
    assert isinstance(error, CairnError)
    assert isinstance(error, RecoverableError)


def test_fatal_error_hierarchy() -> None:
    """Test FatalError is a CairnError."""
    error = FatalError("Permanent failure")
    assert isinstance(error, CairnError)
    assert isinstance(error, FatalError)


def test_path_validation_error() -> None:
    """Test PathValidationError creation."""
    error = PathValidationError(
        "Invalid path",
        error_code="PATH_TRAVERSAL",
        context={"path": "../etc/passwd"},
    )
    assert isinstance(error, ValidationError)
    assert isinstance(error, FatalError)
    assert error.error_code == "PATH_TRAVERSAL"


def test_version_conflict_is_recoverable() -> None:
    """Test VersionConflictError is recoverable."""
    error = VersionConflictError("Version mismatch")
    assert isinstance(error, RecoverableError)
    assert isinstance(error, LifecycleError)
