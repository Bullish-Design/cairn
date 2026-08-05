from __future__ import annotations

from pathlib import Path

import pytest

from cairn.runtime.workspace_cache import WorkspaceCache


class DummyWorkspace:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_cache_eviction_closes_oldest() -> None:
    cache = WorkspaceCache(max_size=2)

    first = DummyWorkspace()
    second = DummyWorkspace()
    third = DummyWorkspace()

    await cache.put("a", first)
    await cache.put("b", second)
    await cache.put("c", third)

    assert cache.size() == 2
    assert first.closed is True
    assert second.closed is False
    assert third.closed is False


@pytest.mark.asyncio
async def test_cache_access_updates_lru_order() -> None:
    cache = WorkspaceCache(max_size=2)

    first = DummyWorkspace()
    second = DummyWorkspace()
    third = DummyWorkspace()

    await cache.put("a", first)
    await cache.put("b", second)

    assert await cache.get("a") is first

    await cache.put("c", third)

    assert first.closed is False
    assert second.closed is True
    assert third.closed is False


@pytest.mark.asyncio
async def test_pinned_workspace_not_evicted(tmp_path: Path) -> None:
    """P4.5: a pinned workspace survives eviction pressure."""
    from fsdantic import Fsdantic

    cache = WorkspaceCache(max_size=1)
    ws1 = await Fsdantic.open(path=str(tmp_path / "one.db"))
    ws2 = await Fsdantic.open(path=str(tmp_path / "two.db"))
    ws3 = await Fsdantic.open(path=str(tmp_path / "three.db"))
    try:
        await cache.put("k1", ws1)
        async with cache.pinned("k1"):
            await cache.put("k2", ws2)
            await cache.put("k3", ws3)
            # Only k2/k3 are evictable; k1 must still be cached and open.
            assert await cache.get("k1") is ws1
        # After unpinning, eviction pressure can drop it.
        await cache.put("k4", ws1)
        assert cache.size() <= 1
    finally:
        for ws in (ws1, ws2, ws3):
            await ws.close()


@pytest.mark.asyncio
async def test_nested_pins_are_reference_counted(tmp_path: Path) -> None:
    """Review §3.6: pins must be reference-counted.  Two nested users of the
    same key count as two pins; when the first exits, the key must stay pinned
    for the second.  (A set-based pin loses the key as soon as either exits.)"""
    from fsdantic import Fsdantic

    cache = WorkspaceCache(max_size=1)
    ws1 = await Fsdantic.open(path=str(tmp_path / "one.db"))
    ws2 = await Fsdantic.open(path=str(tmp_path / "two.db"))
    try:
        await cache.put("k1", ws1)

        async with cache.pinned("k1"):  # user A acquires a pin
            async with cache.pinned("k1"):  # user B acquires a second pin
                pass
            # A is still inside its pin; eviction pressure must not drop k1.
            await cache.put("k2", ws2)

            assert await cache.get("k1") is ws1, "k1 evicted while a second pin was active"
        await cache.clear()
    finally:
        for ws in (ws1, ws2):
            await ws.close()
