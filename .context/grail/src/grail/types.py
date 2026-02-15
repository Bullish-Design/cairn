"""Core type aliases and helpers for Grail."""

from __future__ import annotations

from typing import Final, TypedDict


class ResourceLimits(TypedDict, total=False):
    """Resource limits passed to Monty execution."""

    max_allocations: int
    max_duration_secs: float
    max_memory: int
    gc_interval: int
    max_recursion_depth: int


_DEFAULT_LIMITS: Final[ResourceLimits] = {
    "max_duration_secs": 1.0,
    "max_memory": 16 * 1024 * 1024,
    "max_recursion_depth": 200,
}


def default_resource_limits() -> ResourceLimits:
    """Return baseline resource limits used when callers omit values."""
    return dict(_DEFAULT_LIMITS)


def merge_resource_limits(overrides: ResourceLimits | None) -> ResourceLimits:
    """Merge caller-provided limits over Grail's safe defaults."""
    merged = default_resource_limits()
    if overrides:
        merged.update(overrides)
    return merged
