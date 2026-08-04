"""Retry logic for agent operations.

This module provides retry strategies with exponential backoff
for handling transient failures in async operations, plus the
``with_retry`` decorator (formerly in ``retry_utils``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

T = TypeVar("T")
P = ParamSpec("P")

LOGGER = logging.getLogger(__name__)


class RetryStrategy:
    """Retry failed operations with exponential backoff."""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
    ):
        """Initialize retry strategy.

        Args:
            max_attempts: Maximum number of attempts (default: 3)
            initial_delay: Initial delay in seconds (default: 1.0)
            max_delay: Maximum delay in seconds (default: 60.0)
            backoff_factor: Multiplier for delay after each failure (default: 2.0)
        """
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for retry attempt.

        Args:
            attempt: Attempt number (0-indexed)

        Returns:
            Delay in seconds
        """
        delay = self.initial_delay * (self.backoff_factor**attempt)
        return min(delay, self.max_delay)

    async def with_retry(
        self,
        operation: Callable[[], Awaitable[T]],
        error_handler: Callable[[Exception, int], Awaitable[None]] | None = None,
        retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    ) -> T:
        """Execute operation with retry.

        Args:
            operation: Async function to execute
            error_handler: Optional async function called on each failure
                          with (exception, attempt_number)
            retry_exceptions: Tuple of exception types to retry on

        Returns:
            Result from operation

        Raises:
            Exception: The last exception if all attempts fail
        """
        last_exception = None

        for attempt in range(self.max_attempts):
            try:
                return await operation()

            except retry_exceptions as e:
                last_exception = e

                # Call error handler if provided
                if error_handler:
                    await error_handler(e, attempt)

                # Don't sleep after last attempt
                if attempt < self.max_attempts - 1:
                    delay = self._calculate_delay(attempt)
                    await asyncio.sleep(delay)

        # All attempts failed
        if last_exception:
            raise last_exception
        raise RuntimeError("Retry failed without exception")

    def with_retry_sync(
        self,
        operation: Callable[[], T],
        error_handler: Callable[[Exception, int], None] | None = None,
        retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    ) -> T:
        """Execute a sync operation with retry.

        Delays use ``time.sleep`` (genuinely synchronous); the previous
        implementation called ``run_until_complete`` inside a running loop,
        which always raised.
        """
        last_exception = None

        for attempt in range(self.max_attempts):
            try:
                return operation()

            except retry_exceptions as e:
                last_exception = e

                # Call error handler if provided
                if error_handler:
                    error_handler(e, attempt)

                # Don't sleep after last attempt
                if attempt < self.max_attempts - 1:
                    delay = self._calculate_delay(attempt)
                    time.sleep(delay)

        # All attempts failed
        if last_exception:
            raise last_exception
        raise RuntimeError("Retry failed without exception")


def with_retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    logger: logging.Logger | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Retry an async function with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts.
        initial_delay: Initial delay in seconds before retrying.
        max_delay: Maximum delay in seconds between retries.
        backoff_factor: Multiplier used for exponential backoff delays.
        retry_exceptions: Exception types that should trigger a retry.
        logger: Optional logger used for retry error messages.

    Returns:
        A decorator for async callables.
    """

    retry_logger = logger or LOGGER

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            strategy = RetryStrategy(
                max_attempts=max_attempts,
                initial_delay=initial_delay,
                max_delay=max_delay,
                backoff_factor=backoff_factor,
            )

            async def operation() -> T:
                return await func(*args, **kwargs)

            async def error_handler(error: Exception, attempt: int) -> None:
                retry_logger.warning(
                    "Retryable operation '%s' failed on attempt %d/%d",
                    getattr(func, "__name__", type(func).__name__),
                    attempt + 1,
                    max_attempts,
                    exc_info=error,
                )

            return await strategy.with_retry(
                operation,
                error_handler=error_handler,
                retry_exceptions=retry_exceptions,
            )

        return wrapped

    return decorator
