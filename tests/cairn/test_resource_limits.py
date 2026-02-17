"""Tests for resource limit enforcement utilities."""

from __future__ import annotations

import asyncio

import pytest

from cairn.core.exceptions import ResourceLimitError, TimeoutError as CairnTimeoutError
from cairn.runtime.resource_limits import ResourceLimiter, run_with_timeout


@pytest.mark.asyncio
async def test_run_with_timeout_success() -> None:
    """Test run_with_timeout returns result within limit."""
    result = await run_with_timeout(asyncio.sleep(0.01, result="ok"), timeout_seconds=1.0)
    assert result == "ok"


@pytest.mark.asyncio
async def test_run_with_timeout_exceeds() -> None:
    """Test run_with_timeout raises when timeout exceeded."""
    with pytest.raises(CairnTimeoutError):
        await run_with_timeout(asyncio.sleep(0.2), timeout_seconds=0.01)


@pytest.mark.asyncio
async def test_resource_limiter_allows_small_work() -> None:
    """Test resource limiter allows lightweight work."""
    limiter = ResourceLimiter(timeout_seconds=1.0, max_memory_bytes=50 * 1024 * 1024, poll_interval_seconds=0.01)
    async with limiter.limit():
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_resource_limiter_detects_memory_growth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test resource limiter raises on memory spikes."""
    values = [1000, 1000, 4000]

    def fake_rss() -> int:
        return values.pop(0) if values else 4000

    from cairn.runtime import resource_limits

    monkeypatch.setattr(resource_limits, "_get_rss_bytes", fake_rss)
    limiter = ResourceLimiter(timeout_seconds=1.0, max_memory_bytes=1000, poll_interval_seconds=0.01)
    with pytest.raises(ResourceLimitError):
        async with limiter.limit():
            await asyncio.sleep(0.05)
