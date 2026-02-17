from __future__ import annotations

from cairn.utils.error_formatting import (
    format_agent_error,
    format_lifecycle_error,
    format_workspace_error,
)


def test_format_agent_error_basic() -> None:
    message = format_agent_error("Task failed", agent_id="agent-123")
    assert "Task failed" in message
    assert "agent_id=agent-123" in message


def test_format_agent_error_with_state_and_context() -> None:
    message = format_agent_error(
        "Merge failed",
        agent_id="agent-123",
        state="SUBMITTING",
        conflicts=["file1.py", "file2.py"],
        retry_count=3,
    )
    assert "agent_id=agent-123" in message
    assert "state=SUBMITTING" in message
    assert "conflicts=2" in message
    assert "retry_count=3" in message


def test_format_agent_error_truncates_task() -> None:
    long_task = "A" * 100
    message = format_agent_error("Error", agent_id="agent-123", task=long_task)
    assert "..." in message
    assert "agent_id=agent-123" in message


def test_format_workspace_error() -> None:
    message = format_workspace_error("Merge failed", "/path/to/ws", operation="merge")
    assert "Merge failed" in message
    assert "workspace=/path/to/ws" in message
    assert "operation=merge" in message


def test_format_lifecycle_error() -> None:
    message = format_lifecycle_error("Version conflict", agent_id="agent-123", version=5)
    assert "Version conflict" in message
    assert "agent_id=agent-123" in message
    assert "version=5" in message
