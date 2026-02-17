"""Decorator helpers for retrying async operations."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from cairn.utils.retry import RetryStrategy

P = ParamSpec("P")
T = TypeVar("T")

LOGGER = logging.getLogger(__name__)


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
                    func.__name__,
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
