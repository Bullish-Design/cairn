"""Helper functions for orchestrator operations.

This module contains utilities extracted from orchestrator logic to keep the
main runtime focused on lifecycle coordination.
"""

from __future__ import annotations


def calculate_priority_score(priority: int, created_at: float) -> tuple[int, float]:
    """Calculate a sort key for priority queue ordering."""
    return (-int(priority), created_at)
