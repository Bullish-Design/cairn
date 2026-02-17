"""Workspace lifecycle management with automatic cleanup.

This module provides context managers and utilities for ensuring proper
workspace resource cleanup even in error scenarios.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fsdantic import Fsdantic, Workspace

from cairn.core.exceptions import WorkspaceError

logger = logging.getLogger(__name__)


async def _open_workspace(path: Path | str, *, readonly: bool) -> Workspace:
    try:
        signature = inspect.signature(Fsdantic.open)
    except (TypeError, ValueError):
        signature = None

    if signature and "readonly" in signature.parameters:
        return await Fsdantic.open(path=str(path), readonly=readonly)
    return await Fsdantic.open(path=str(path))


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

    @asynccontextmanager
    async def open_workspace(
        self,
        path: Path | str,
        *,
        readonly: bool = False,
    ) -> AsyncIterator[Workspace]:
        """Open a workspace with automatic cleanup."""
        workspace: Workspace | None = None
        try:
            workspace = await _open_workspace(path, readonly=readonly)
        except WorkspaceError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise WorkspaceError(
                f"Failed to open workspace: {path}",
                error_code="WORKSPACE_OPEN_FAILED",
                context={"path": str(path), "readonly": readonly},
            ) from exc

        self._active_workspaces.add(workspace)
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
