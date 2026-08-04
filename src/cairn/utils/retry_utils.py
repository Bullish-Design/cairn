"""Deprecated re-export shim for the retry decorator.

``with_retry`` now lives in :mod:`cairn.utils.retry`.  Import it from there;
this module exists for one release for backward compatibility.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from cairn.utils.retry import RetryStrategy
from cairn.utils.retry import with_retry as _original_with_retry

P = ParamSpec("P")
T = TypeVar("T")

__all__ = ["RetryStrategy", "with_retry"]


def with_retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    logger: logging.Logger | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Deprecated alias for ``cairn.utils.retry.with_retry``."""
    warnings.warn(
        "cairn.utils.retry_utils.with_retry is deprecated; import from cairn.utils.retry",
        DeprecationWarning,
        stacklevel=2,
    )
    return _original_with_retry(
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        max_delay=max_delay,
        backoff_factor=backoff_factor,
        retry_exceptions=retry_exceptions,
        logger=logger,
    )
