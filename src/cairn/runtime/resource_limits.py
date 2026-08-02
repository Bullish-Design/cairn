"""Timeout enforcement for agent execution.

Resource limits for sandboxed code are enforced in two places:

- **Inside the sandbox** — the bootstrap script applies ``RLIMIT_DATA``/
  ``RLIMIT_AS`` (memory), ``RLIMIT_CPU`` (CPU time), and recursion depth
  limits to its own process before running task code.
- **On the host** — :func:`run_with_timeout` bounds the wall-clock time of the
  sandbox subprocess (which is then killed).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from cairn.core.constants import DEFAULT_EXECUTION_TIMEOUT_SECONDS
from cairn.core.exceptions import TimeoutError as CairnTimeoutError

T = TypeVar("T")


async def run_with_timeout(
    coro: Awaitable[T],
    *,
    timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
) -> T:
    """Run coroutine with timeout.

    Args:
        coro: Coroutine to run
        timeout_seconds: Maximum execution time

    Returns:
        Result of coroutine

    Raises:
        TimeoutError: If execution exceeds timeout
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise CairnTimeoutError(
            f"Operation exceeded timeout of {timeout_seconds}s",
            error_code="EXECUTION_TIMEOUT",
            context={"timeout_seconds": timeout_seconds},
        ) from exc
