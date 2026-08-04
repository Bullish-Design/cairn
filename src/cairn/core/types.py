"""Type definitions for Cairn operations.

This module provides TypedDict definitions and type aliases for improved
 type safety throughout the Cairn codebase.
"""

from __future__ import annotations

from typing import Protocol, TypedDict


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


class FileEntryProtocol(Protocol):
    """Protocol for file entries returned from workspace queries."""

    path: str
    content: str | bytes | None
