"""Workspace lifecycle management with automatic cleanup.

This module provides context managers and utilities for ensuring proper
workspace resource cleanup even in error scenarios.

Concurrency guarantees
----------------------
Workspaces are backed by Turso (libSQL), which provides concurrency features
beyond vanilla SQLite:

* **WAL mode** is enabled by default on all workspaces.  This allows unlimited
  concurrent readers alongside a single writer on the same database file.
  Read operations never block writes and vice versa.

* **MVCC + BEGIN CONCURRENT** can be enabled via ``enable_mvcc=True``.  This
  uses libSQL's optimistic multi-version concurrency control to allow multiple
  connections to write concurrently to the same database.  Non-conflicting
  writes (different rows/pages) succeed; conflicting writes raise
  ``DatabaseError`` at execute time, not commit time.

* Each ``turso.aio.Connection`` serializes its own operations via a dedicated
  worker thread and ``SimpleQueue``.  This means that a single workspace
  connection is inherently thread-safe for sequential async access -- callers
  do **not** need ``asyncio.Lock`` for operations on a single workspace.

For concurrent write access to the same database file, open multiple
workspaces with ``enable_mvcc=True`` pointing to the same path.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fsdantic import Fsdantic, Workspace

from cairn.core.exceptions import WorkspaceError

logger = logging.getLogger(__name__)


async def _open_workspace(
    path: Path | str,
    *,
    readonly: bool,
    enable_wal: bool = True,
    enable_mvcc: bool = False,
) -> Workspace:
    """Open a workspace via fsdantic with optional WAL and MVCC.

    All concurrency configuration is delegated to ``Fsdantic.open()``.
    """
    return await Fsdantic.open(
        path=str(path),
        enable_wal=enable_wal,
        enable_mvcc=enable_mvcc,
    )


async def open_workspace(
    path: Path | str,
    *,
    readonly: bool = False,
    enable_wal: bool = True,
    enable_mvcc: bool = False,
) -> Workspace:
    """Open a Cairn workspace with Turso-optimized concurrency.

    This is the public API for opening workspaces.  The caller is responsible
    for closing the workspace when done.

    By default, WAL mode is enabled for concurrent read access.  For
    concurrent write access to the same database file from multiple
    connections, pass ``enable_mvcc=True``.

    Args:
        path: Path to the workspace database file.
        readonly: If True, open in read-only mode (default: False).
        enable_wal: If True (default), enable WAL journal mode for
            concurrent read access alongside writes.
        enable_mvcc: If True, enable MVCC with ``BEGIN CONCURRENT``
            support for optimistic concurrent writes from multiple
            connections.  Requires WAL mode (enabled automatically).

    Returns:
        An open Workspace instance.

    Raises:
        WorkspaceError: If the workspace cannot be opened.

    Concurrency notes:
        * Single connection: operations are serialized by the turso worker
          thread.  No application-level locking needed.
        * WAL mode (default): unlimited concurrent readers, single writer.
        * MVCC mode: multiple connections can write concurrently.
          Non-conflicting writes succeed; conflicting writes raise
          ``DatabaseError`` at execute time.

    Example:
        workspace = await open_workspace("/path/to/workspace.db")
        try:
            content = await workspace.files.read("/file.txt")
        finally:
            await workspace.close()
    """
    if enable_mvcc:
        enable_wal = True

    try:
        return await _open_workspace(
            path,
            readonly=readonly,
            enable_wal=enable_wal,
            enable_mvcc=enable_mvcc,
        )
    except WorkspaceError:
        raise
    except Exception as exc:
        raise WorkspaceError(
            f"Failed to open workspace: {path}",
            error_code="WORKSPACE_OPEN_FAILED",
            context={
                "path": str(path),
                "readonly": readonly,
                "enable_wal": enable_wal,
                "enable_mvcc": enable_mvcc,
            },
        ) from exc


class WorkspaceManager:
    """Manages workspace lifecycle with automatic cleanup."""

    def __init__(self) -> None:
        self._active_workspaces: set[Workspace] = set()
        self._closed = False

    def track_workspace(self, workspace: Workspace) -> None:
        """Track an existing workspace for later cleanup."""
        self._active_workspaces.add(workspace)

    def untrack_workspace(self, workspace: Workspace) -> None:
        """Remove a workspace from the active tracking list."""
        self._active_workspaces.discard(workspace)

    async def create_workspace(
        self,
        path: Path | str,
        *,
        readonly: bool = False,
        enable_wal: bool = True,
        enable_mvcc: bool = False,
    ) -> Workspace:
        """Open a workspace, track it, and return it.

        Unlike :meth:`open_workspace` (a context manager), this method
        returns the workspace directly for callers that manage lifetime
        manually via :meth:`close_all`.
        """
        workspace: Workspace | None = None
        try:
            workspace = await _open_workspace(
                path,
                readonly=readonly,
                enable_wal=enable_wal,
                enable_mvcc=enable_mvcc,
            )
        except WorkspaceError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise WorkspaceError(
                f"Failed to open workspace: {path}",
                error_code="WORKSPACE_OPEN_FAILED",
                context={"path": str(path), "readonly": readonly},
            ) from exc

        self._active_workspaces.add(workspace)
        return workspace

    @asynccontextmanager
    async def open_workspace(
        self,
        path: Path | str,
        *,
        readonly: bool = False,
        enable_wal: bool = True,
        enable_mvcc: bool = False,
    ) -> AsyncIterator[Workspace]:
        """Open a workspace with automatic cleanup on exit."""
        workspace = await self.create_workspace(
            path,
            readonly=readonly,
            enable_wal=enable_wal,
            enable_mvcc=enable_mvcc,
        )
        try:
            yield workspace
        finally:
            await self.close_workspace(workspace, path=path)

    @asynccontextmanager
    async def manage_workspace(
        self,
        workspace: Workspace,
        *,
        path: Path | str | None = None,
    ) -> AsyncIterator[Workspace]:
        """Ensure an existing workspace is closed on exit."""
        self._active_workspaces.add(workspace)
        try:
            yield workspace
        finally:
            await self.close_workspace(workspace, path=path)

    async def close_workspace(self, workspace: Workspace, *, path: Path | str | None = None) -> None:
        """Close a workspace and remove it from tracking."""
        try:
            await workspace.close()
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.warning(
                "Failed to close workspace",
                exc_info=exc,
                extra={"path": str(path) if path is not None else None},
            )
        finally:
            self._active_workspaces.discard(workspace)

    async def close_all(self) -> None:
        """Close all active workspaces."""
        if self._closed:
            return

        self._closed = True
        workspaces = list(self._active_workspaces)
        self._active_workspaces.clear()

        if not workspaces:
            return

        results = await asyncio.gather(
            *(self._close_workspace_without_tracking(workspace) for workspace in workspaces),
            return_exceptions=True,
        )

        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            logger.warning(
                "Errors during workspace cleanup",
                extra={"error_count": len(errors)},
            )

    async def _close_workspace_without_tracking(self, workspace: Workspace) -> None:
        try:
            await workspace.close()
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.warning("Failed to close workspace", exc_info=exc)
