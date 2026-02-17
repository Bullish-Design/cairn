"""Tests for type definitions and type safety."""

from typing import get_type_hints

import pytest

from cairn.runtime.external_functions import CairnExternalFunctions
from cairn.core.types import Result, SearchContentMatchData, SubmissionData


def test_search_content_match_structure() -> None:
    """Test SearchContentMatchData TypedDict structure."""
    match: SearchContentMatchData = {"file": "test.py", "line": 42, "text": "def foo():"}
    assert isinstance(match["file"], str)
    assert isinstance(match["line"], int)
    assert isinstance(match["text"], str)


def test_submission_data_structure() -> None:
    """Test SubmissionData TypedDict structure."""
    submission: SubmissionData = {
        "summary": "done",
        "changed_files": ["notes/todo.txt"],
        "submitted_at": 1.23,
    }
    assert isinstance(submission["summary"], str)
    assert isinstance(submission["changed_files"], list)
    assert isinstance(submission["submitted_at"], float)


def test_result_ok() -> None:
    """Test Result.ok() creates successful result."""
    result = Result.ok(42)
    assert result.is_ok()
    assert not result.is_error()
    assert result.unwrap() == 42


def test_result_error() -> None:
    """Test Result.error() creates error result."""
    result = Result.error("Something failed")
    assert result.is_error()
    assert not result.is_ok()
    assert result.error_message() == "Something failed"


def test_result_unwrap_error_raises() -> None:
    """Test unwrapping error result raises."""
    result = Result.error("Failed")
    with pytest.raises(ValueError, match="Cannot unwrap error result"):
        result.unwrap()


def test_result_unwrap_or() -> None:
    """Test unwrap_or returns default on error."""
    result = Result.error("Failed")
    assert result.unwrap_or(999) == 999

    result_ok = Result.ok(42)
    assert result_ok.unwrap_or(999) == 42


def test_external_function_return_types() -> None:
    """Test external functions have correct return type hints."""
    hints = get_type_hints(CairnExternalFunctions.search_content)
    assert "return" in hints
