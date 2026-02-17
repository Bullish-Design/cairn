"""Tests for constants module."""

from cairn.core.constants import (
    DAY,
    GB,
    HOUR,
    KB,
    MB,
    MINUTE,
    SECOND,
    WEEK,
    LIFECYCLE_CLEANUP_MAX_AGE_SECONDS,
    MAX_FILE_SIZE_BYTES,
)


def test_time_constants() -> None:
    """Test time constants are correct."""
    assert SECOND == 1.0
    assert MINUTE == 60.0
    assert HOUR == 3600.0
    assert DAY == 86400.0
    assert WEEK == 604800.0


def test_size_constants() -> None:
    """Test size constants are correct."""
    assert KB == 1024
    assert MB == 1024 * 1024
    assert GB == 1024 * 1024 * 1024


def test_max_file_size() -> None:
    """Test max file size is reasonable."""
    assert MAX_FILE_SIZE_BYTES == 10 * MB
    assert MAX_FILE_SIZE_BYTES == 10485760


def test_lifecycle_cleanup_age() -> None:
    """Test lifecycle cleanup age matches week."""
    assert LIFECYCLE_CLEANUP_MAX_AGE_SECONDS == WEEK
    assert LIFECYCLE_CLEANUP_MAX_AGE_SECONDS == 604800.0
