"""Tests for safe regex utilities."""

from __future__ import annotations

import time
from typing import Pattern, cast

import pytest

from cairn.core.exceptions import SecurityError
from cairn.utils.regex_utils import RegexTimeoutError, compile_safe_regex, search_with_timeout


class SlowPattern:
    """Pattern-like object that sleeps during search."""

    def search(self, text: str) -> None:
        time.sleep(0.2)
        return None


def test_compile_safe_regex_success() -> None:
    """Test compiling safe regex pattern."""
    pattern = compile_safe_regex(r"\d+")
    assert pattern is not None


def test_compile_safe_regex_too_long() -> None:
    """Test rejecting too-long patterns."""
    long_pattern = "a" * 2000
    with pytest.raises(SecurityError, match="too long"):
        compile_safe_regex(long_pattern)


def test_compile_safe_regex_dangerous_pattern() -> None:
    """Test rejecting dangerous nested quantifiers."""
    with pytest.raises(SecurityError, match="dangerous"):
        compile_safe_regex(r"(.*)+")


@pytest.mark.asyncio
async def test_search_with_timeout_success() -> None:
    """Test regex search succeeds within timeout."""
    pattern = compile_safe_regex(r"\d+")
    result = await search_with_timeout(pattern, "test 123 data", timeout=1.0)
    assert result is not None
    assert result.group(0) == "123"


@pytest.mark.asyncio
async def test_search_with_timeout_exceeds() -> None:
    """Test regex search timeout on slow patterns."""
    slow_pattern = cast(Pattern[str], SlowPattern())
    with pytest.raises(RegexTimeoutError):
        await search_with_timeout(slow_pattern, "test", timeout=0.01)
