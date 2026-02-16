"""Retry logic for agent operations.

This module provides retry strategies with exponential backoff
for handling transient failures in async operations.
"""

import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")


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
        error_handler: Optional[Callable[[Exception, int], Awaitable[None]]] = None,
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

    async def with_retry_sync(
        self,
        operation: Callable[[], T],
        error_handler: Optional[Callable[[Exception, int], None]] = None,
        retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    ) -> T:
        """Execute sync operation with retry.

        Args:
            operation: Sync function to execute
            error_handler: Optional sync function called on each failure
            retry_exceptions: Tuple of exception types to retry on

        Returns:
            Result from operation

        Raises:
            Exception: The last exception if all attempts fail
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
                    asyncio.get_event_loop().run_until_complete(asyncio.sleep(delay))

        # All attempts failed
        if last_exception:
            raise last_exception
        raise RuntimeError("Retry failed without exception")
