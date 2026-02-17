from __future__ import annotations

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
