"""Safe regex utilities with ReDoS protection.

This module provides regex compilation and matching with timeout protection
to prevent Regular Expression Denial of Service (ReDoS) attacks.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Pattern

from cairn.core.constants import REGEX_MAX_MATCHES, REGEX_MAX_PATTERN_LENGTH, REGEX_TIMEOUT_SECONDS
from cairn.core.exceptions import SecurityError, TimeoutError as CairnTimeoutError


logger = logging.getLogger(__name__)


class RegexTimeoutError(CairnTimeoutError):
    """Regex execution exceeded timeout - possible ReDoS attack."""


_DANGEROUS_REGEX_PATTERNS = (
    r"\(\.\*\)\+",
    r"\(\.\+\)\*",
    r"\(\.\*\)\*",
    r"\(\.\+\)\+",
)


def compile_safe_regex(
    pattern: str,
    *,
    flags: int = 0,
    timeout: float = REGEX_TIMEOUT_SECONDS,
) -> Pattern[str]:
    """Compile regex pattern with validation.

    Args:
        pattern: Regex pattern to compile
        flags: Regex flags (re.IGNORECASE, etc.)
        timeout: Timeout for pattern compilation

    Returns:
        Compiled regex pattern

    Raises:
        SecurityError: If pattern is potentially dangerous
        ValueError: If pattern is invalid
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    if len(pattern) > REGEX_MAX_PATTERN_LENGTH:
        raise SecurityError(
            "Regex pattern too long - possible DoS attempt",
            error_code="REGEX_TOO_LONG",
            context={"pattern_length": len(pattern)},
        )

    for danger in _DANGEROUS_REGEX_PATTERNS:
        if re.search(danger, pattern):
            raise SecurityError(
                "Regex pattern contains dangerous nested quantifiers",
                error_code="REGEX_DANGEROUS_PATTERN",
                context={"pattern": pattern[:100]},
            )

    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc


async def search_with_timeout(
    pattern: Pattern[str],
    text: str,
    *,
    timeout: float = REGEX_TIMEOUT_SECONDS,
) -> re.Match[str] | None:
    """Search text with regex pattern and timeout.

    Args:
        pattern: Compiled regex pattern
        text: Text to search
        timeout: Maximum time in seconds

    Returns:
        Match object or None

    Raises:
        RegexTimeoutError: If search exceeds timeout
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(pattern.search, text), timeout=timeout)
    except asyncio.TimeoutError as exc:
        pattern_value = getattr(pattern, "pattern", "<unknown>")
        raise RegexTimeoutError(
            f"Regex search exceeded timeout of {timeout}s - possible ReDoS",
            error_code="REGEX_TIMEOUT",
            context={
                "timeout": timeout,
                "text_length": len(text),
                "pattern": str(pattern_value)[:100],
            },
        ) from exc


async def findall_with_timeout(
    pattern: Pattern[str],
    text: str,
    *,
    timeout: float = REGEX_TIMEOUT_SECONDS,
    max_matches: int = REGEX_MAX_MATCHES,
) -> list[str]:
    """Find all matches with timeout and limit.

    Args:
        pattern: Compiled regex pattern
        text: Text to search
        timeout: Maximum time in seconds
        max_matches: Maximum number of matches to return

    Returns:
        List of matches (limited to max_matches)

    Raises:
        RegexTimeoutError: If search exceeds timeout
    """
    try:
        result = await asyncio.wait_for(asyncio.to_thread(pattern.findall, text), timeout=timeout)
    except asyncio.TimeoutError as exc:
        pattern_value = getattr(pattern, "pattern", "<unknown>")
        raise RegexTimeoutError(
            f"Regex findall exceeded timeout of {timeout}s - possible ReDoS",
            error_code="REGEX_TIMEOUT",
            context={
                "timeout": timeout,
                "text_length": len(text),
                "pattern": str(pattern_value)[:100],
            },
        ) from exc

    if len(result) > max_matches:
        logger.warning(
            "Regex match count exceeded limit",
            extra={"total_matches": len(result), "max_matches": max_matches},
        )
        return result[:max_matches]

    return result
