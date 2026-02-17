"""Resource limit enforcement for agent execution.

This module provides utilities for enforcing CPU time, memory, and wall-clock
time limits on agent code execution.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator, Awaitable, TypeVar

from cairn.core.constants import DEFAULT_EXECUTION_TIMEOUT_SECONDS, DEFAULT_MAX_MEMORY_BYTES
from cairn.core.exceptions import ResourceLimitError, TimeoutError as CairnTimeoutError

try:
    import resource
except ImportError:
    resource = None


logger = logging.getLogger(__name__)


def _get_rss_bytes() -> int | None:
    if resource is None:
        return None

    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = usage.ru_maxrss
    if sys.platform == "darwin":
        return rss
    return rss * 1024


class ResourceLimiter:
    """Enforce resource limits on code execution."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        """Initialize resource limiter.

        Args:
            timeout_seconds: Maximum wall-clock time
            max_memory_bytes: Maximum memory usage
            poll_interval_seconds: Interval for memory checks
        """
        self.timeout_seconds = timeout_seconds
        self.max_memory_bytes = max_memory_bytes
        self.poll_interval_seconds = poll_interval_seconds

    @asynccontextmanager
    async def limit(self) -> AsyncIterator[None]:
        """Context manager to enforce resource limits.

        Raises:
            TimeoutError: If execution exceeds time limit
            ResourceLimitError: If execution exceeds memory limits
        """
        error: ResourceLimitError | None = None
        error_raised = False
        current_task = asyncio.current_task()
        start_rss = _get_rss_bytes()
        previous_limits = self._apply_resource_limits()

        async def monitor_resources() -> None:
            nonlocal error
            if start_rss is None:
                return

            while True:
                await asyncio.sleep(self.poll_interval_seconds)
                current_rss = _get_rss_bytes()
                if current_rss is None:
                    continue

                delta = max(0, current_rss - start_rss)
                if delta > self.max_memory_bytes:
                    error = ResourceLimitError(
                        "Memory limit exceeded",
                        error_code="MEMORY_LIMIT_EXCEEDED",
                        context={
                            "current_bytes": current_rss,
                            "limit_bytes": self.max_memory_bytes,
                        },
                    )
                    if current_task is not None:
                        current_task.cancel()
                    return

        monitor_task = asyncio.create_task(monitor_resources())

        try:
            yield
        except asyncio.CancelledError:
            if error is not None:
                error_raised = True
                raise error
            raise
        finally:
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task
            self._restore_resource_limits(previous_limits)
            if error is not None and not error_raised:
                raise error

    def _apply_resource_limits(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        if resource is None:
            return None

        try:
            cpu_limits = resource.getrlimit(resource.RLIMIT_CPU)
            mem_limits = resource.getrlimit(resource.RLIMIT_AS)
        except (ValueError, OSError) as exc:
            logger.warning("Could not read resource limits", extra={"error": str(exc)})
            return None

        soft_cpu = int(self.timeout_seconds)
        hard_cpu = cpu_limits[1]
        if soft_cpu > hard_cpu:
            soft_cpu = hard_cpu
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (soft_cpu, hard_cpu))
        except (ValueError, OSError) as exc:
            logger.warning("Could not set CPU limit", extra={"error": str(exc)})

        current_rss = _get_rss_bytes()
        hard_limit = mem_limits[1]
        soft_limit = min(self.max_memory_bytes, hard_limit)
        if current_rss is not None and soft_limit <= current_rss:
            logger.warning(
                "Skipping memory limit below current usage",
                extra={"current_bytes": current_rss, "limit_bytes": soft_limit},
            )
        else:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (soft_limit, hard_limit))
            except (ValueError, OSError) as exc:
                logger.warning("Could not set memory limit", extra={"error": str(exc)})

        return cpu_limits, mem_limits

    @staticmethod
    def _restore_resource_limits(
        previous_limits: tuple[tuple[int, int], tuple[int, int]] | None,
    ) -> None:
        if resource is None or previous_limits is None:
            return

        cpu_limits, mem_limits = previous_limits
        with suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, cpu_limits)
        with suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, mem_limits)


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
    except asyncio.TimeoutError as exc:
        raise CairnTimeoutError(
            f"Operation exceeded timeout of {timeout_seconds}s",
            error_code="EXECUTION_TIMEOUT",
            context={"timeout_seconds": timeout_seconds},
        ) from exc
