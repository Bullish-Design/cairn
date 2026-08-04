"""Tests for type definitions and type safety."""

from cairn.core.types import SearchContentMatchData, SubmissionData


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
