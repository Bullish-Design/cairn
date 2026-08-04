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
  connections to write concurrently to the same database.  Caveat (verified on
  pyturso 0.7.2): the driver does **not** reliably surface write-write
  conflicts — each ``connect()`` opens an independent MVCC store, so
  concurrent same-row writes are effectively last-write-wins with no error to
  catch and retry on.  For atomic read-modify-write sequences use
  ``Workspace.serialized()`` (a same-process per-workspace lock) or the
  repository's SQL compare-and-set (``compare_and_set``, versioned ``save()``).

* Each ``turso.aio.Connection`` serializes its own operations via a dedicated
  worker thread and ``SimpleQueue``.  This means that a single workspace
  connection is inherently thread-safe for sequential async access -- callers
  do **not** need ``asyncio.Lock`` for operations on a single workspace.

For concurrent write access to the same database file, open multiple
workspaces with ``enable_mvcc=True`` pointing to the same path, or serialize
read-modify-write sequences with ``Workspace.serialized()``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fsdantic import Fsdantic, Workspace
from fsdantic.exceptions import WorkspaceError as FsdanticWorkspaceError

from cairn.core.exceptions import WorkspaceError

logger = logging.getLogger(__name__)


async def _open_workspace(
    path: Path | str,
    *,
    readonly: bool = False,
    enable_wal: bool = True,
    enable_mvcc: bool = False,
    busy_timeout_ms: int = 5000,
    max_content_bytes: int | None = None,
) -> Workspace:
    """Open a workspace via fsdantic with optional concurrency/read-only config.

    All configuration is delegated to ``Fsdantic.open()`` (fsdantic >= 0.5.0):
    WAL/MVCC journal modes, read-only mode, write-lock busy timeout, and an
    optional cap on write payload sizes.
    """
    return await Fsdantic.open(
        path=str(path),
        enable_wal=enable_wal,
        enable_mvcc=enable_mvcc,
        readonly=readonly,
        busy_timeout_ms=busy_timeout_ms,
        max_content_bytes=max_content_bytes,
    )


async def open_workspace(
    path: Path | str,
    *,
    readonly: bool = False,
    enable_wal: bool = True,
    enable_mvcc: bool = False,
    busy_timeout_ms: int = 5000,
    max_content_bytes: int | None = None,
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
            Write operations raise ``WorkspaceError``
            (``WORKSPACE_READONLY``); the database file must already exist
            (``WORKSPACE_NOT_FOUND`` otherwise).  Reads never write: the
            SDK's access-time maintenance write is neutralized.
        enable_wal: If True (default), enable WAL journal mode for
            concurrent read access alongside writes.
        enable_mvcc: If True, enable MVCC with ``BEGIN CONCURRENT``
            support for optimistic concurrent writes from multiple
            connections.  Requires WAL mode (enabled automatically).
        busy_timeout_ms: Maximum milliseconds a write waits on a contended
            write lock before failing with "database is locked" (default
            5000; ``0`` disables the wait).
        max_content_bytes: Optional cap on write payload sizes
            (``files.write``/``write_many`` payloads and ``kv.set``/
            ``set_many`` JSON payloads).  Oversized payloads raise
            ``WorkspaceError`` (``CONTENT_TOO_LARGE``).  ``None`` (default)
            is unbounded.

    Returns:
        An open Workspace instance.

    Raises:
        WorkspaceError: If the workspace cannot be opened.

    Concurrency notes:
        * Single connection: operations are serialized by the turso worker
          thread.  No application-level locking needed.
        * WAL mode (default): unlimited concurrent readers, single writer.
        * MVCC mode: multiple connections can write concurrently.
          Write-write conflicts are NOT reliably surfaced by the driver
          (last-write-wins) — use ``Workspace.serialized()`` or the
          repository CAS for atomic read-modify-write sequences.

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
            busy_timeout_ms=busy_timeout_ms,
            max_content_bytes=max_content_bytes,
        )
    except FsdanticWorkspaceError as exc:
        # Translate fsdantic workspace errors, preserving the meaningful
        # codes (WORKSPACE_NOT_FOUND, WORKSPACE_READONLY, CONTENT_TOO_LARGE)
        # instead of collapsing them into a generic open failure.
        raise WorkspaceError(
            str(exc),
            error_code=getattr(exc, "code", None) or "WORKSPACE_ERROR",
            context={
                "path": str(path),
                "readonly": readonly,
                "enable_wal": enable_wal,
                "enable_mvcc": enable_mvcc,
                "busy_timeout_ms": busy_timeout_ms,
                "max_content_bytes": max_content_bytes,
                "fsdantic_code": getattr(exc, "code", None),
            },
        ) from exc
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
                "busy_timeout_ms": busy_timeout_ms,
                "max_content_bytes": max_content_bytes,
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
        busy_timeout_ms: int = 5000,
        max_content_bytes: int | None = None,
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
                busy_timeout_ms=busy_timeout_ms,
                max_content_bytes=max_content_bytes,
            )
        except FsdanticWorkspaceError as exc:
            raise WorkspaceError(
                str(exc),
                error_code=getattr(exc, "code", None) or "WORKSPACE_ERROR",
                context={"path": str(path), "readonly": readonly, "fsdantic_code": getattr(exc, "code", None)},
            ) from exc
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
        busy_timeout_ms: int = 5000,
        max_content_bytes: int | None = None,
    ) -> AsyncIterator[Workspace]:
        """Open a workspace with automatic cleanup on exit."""
        workspace = await self.create_workspace(
            path,
            readonly=readonly,
            enable_wal=enable_wal,
            enable_mvcc=enable_mvcc,
            busy_timeout_ms=busy_timeout_ms,
            max_content_bytes=max_content_bytes,
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
