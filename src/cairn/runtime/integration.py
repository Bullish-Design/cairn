"""Project integration lock — the single writer gate for tree mutation.

All mutation of the actual working tree (accept, undo, and their crash
recovery) holds one exclusive per-project lock (review §3.2, §4.2 step 6).
The lock is a `flock` on a lockfile inside the project's metadata directory
(``.agentfs/integration.lock``), so:

- two processes (daemon, inline ``cairn run``, a future watcher) can never
  apply changesets concurrently,
- accept and undo serialize against each other,
- the lock is released automatically on process death (kernel releases
  ``flock``), so it cannot go stale.

``flock`` is an advisory lock on a held file descriptor — not a
check-then-replace pidfile (review §3.7).
"""

from __future__ import annotations

import asyncio
import fcntl
import os
from pathlib import Path
from typing import Self


class IntegrationLock:
    """Exclusive per-project lock, acquired off the event loop (blocking flock)."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    def _acquire(self) -> int:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    @staticmethod
    def _release(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    async def __aenter__(self) -> Self:
        self._fd = await asyncio.to_thread(self._acquire)
        return self

    async def __aexit__(self, *exc: object) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            await asyncio.to_thread(self._release, fd)
