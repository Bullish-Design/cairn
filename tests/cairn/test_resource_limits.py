"""Tests for resource limit enforcement utilities.

Memory/CPU limits for sandboxed code are enforced inside the sandbox via
rlimits (see ``cairn.runtime.sandbox.boot``); the host-side helper here only
bounds wall-clock time on the subprocess.
"""

from __future__ import annotations

import asyncio

import pytest

from cairn.core.exceptions import TimeoutError as CairnTimeoutError
from cairn.runtime.resource_limits import run_with_timeout


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
