"""LRU cache for workspace objects to limit memory usage."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from cairn.core.constants import MAX_WORKSPACE_CACHE_SIZE

logger = logging.getLogger(__name__)


class WorkspaceCache:
    """LRU cache for workspace objects with size limit."""

    def __init__(self, max_size: int = MAX_WORKSPACE_CACHE_SIZE) -> None:
        """Initialize workspace cache.

        Args:
            max_size: Maximum number of cached workspaces
        """
        self.max_size = max_size
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Get workspace from cache.

        Args:
            key: Cache key (typically workspace path)

        Returns:
            Cached workspace or None if not found
        """
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    async def put(self, key: str, workspace: Any) -> None:
        """Put workspace in cache.

        Args:
            key: Cache key
            workspace: Workspace object to cache
        """
        async with self._lock:
            if key in self._cache:
                self._cache[key] = workspace
                self._cache.move_to_end(key)
            else:
                self._cache[key] = workspace
            await self._evict_if_needed()

    async def remove(self, key: str) -> bool:
        """Remove workspace from cache.

        Args:
            key: Cache key

        Returns:
            True if the workspace was removed.
        """
        async with self._lock:
            workspace = self._cache.pop(key, None)
        if workspace is not None:
            await self._close_workspace(workspace, key=key)
            return True
        return False

    async def clear(self) -> None:
        """Clear all cached workspaces."""
        async with self._lock:
            workspaces = list(self._cache.items())
            self._cache.clear()
        for key, workspace in workspaces:
            await self._close_workspace(workspace, key=key)

    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)

    async def _evict_if_needed(self) -> None:
        while self.max_size > 0 and len(self._cache) > self.max_size:
            oldest_key, oldest_workspace = self._cache.popitem(last=False)
            await self._close_workspace(oldest_workspace, key=oldest_key)

    async def _close_workspace(self, workspace: Any, *, key: str) -> None:
        close_method = getattr(workspace, "close", None)
        if close_method is None:
            return
        try:
            await close_method()
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.warning("Failed to close evicted workspace", exc_info=exc, extra={"key": key})
