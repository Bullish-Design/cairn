"""Utilities for consistent error message formatting.

This module provides helper functions for creating informative error messages
with consistent context information.
"""

from __future__ import annotations

from typing import Any


def format_agent_error(
    message: str,
    agent_id: str,
    *,
    state: str | None = None,
    task: str | None = None,
    **context: Any,
) -> str:
    """Format error message with agent context."""
    parts = [message]
    ctx_parts = [f"agent_id={agent_id}"]

    if state:
        ctx_parts.append(f"state={state}")

    if task:
        task_preview = task[:50] + "..." if len(task) > 50 else task
        ctx_parts.append(f"task={task_preview!r}")

    for key, value in context.items():
        if isinstance(value, list):
            ctx_parts.append(f"{key}={len(value)}")
        elif isinstance(value, dict):
            ctx_parts.append(f"{key}={len(value)} items")
        else:
            ctx_parts.append(f"{key}={value}")

    parts.append(f"[{', '.join(ctx_parts)}]")
    return " ".join(parts)


def format_workspace_error(
    message: str,
    workspace_path: str,
    *,
    operation: str | None = None,
    **context: Any,
) -> str:
    """Format error message with workspace context."""
    parts = [message]
    ctx_parts = [f"workspace={workspace_path}"]

    if operation:
        ctx_parts.append(f"operation={operation}")

    for key, value in context.items():
        ctx_parts.append(f"{key}={value}")

    parts.append(f"[{', '.join(ctx_parts)}]")
    return " ".join(parts)


def format_lifecycle_error(
    message: str,
    agent_id: str,
    *,
    version: int | None = None,
    **context: Any,
) -> str:
    """Format error message with lifecycle context."""
    parts = [message]
    ctx_parts = [f"agent_id={agent_id}"]

    if version is not None:
        ctx_parts.append(f"version={version}")

    for key, value in context.items():
        ctx_parts.append(f"{key}={value}")

    parts.append(f"[{', '.join(ctx_parts)}]")
    return " ".join(parts)
