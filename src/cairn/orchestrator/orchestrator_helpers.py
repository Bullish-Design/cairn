"""Helper functions for orchestrator operations.

This module contains utilities extracted from orchestrator logic to keep the
main runtime focused on lifecycle coordination.
"""

from __future__ import annotations

from pathlib import Path

from fsdantic import ViewQuery, Workspace

from cairn.core.exceptions import WorkspaceError
from cairn.core.types import FileEntryProtocol


def calculate_priority_score(priority: int, created_at: float) -> tuple[int, float]:
    """Calculate a sort key for priority queue ordering."""
    return (-int(priority), created_at)


async def copy_workspace_to_submission(workspace: Workspace, destination: Path) -> None:
    """Copy workspace contents to a submission directory.

    Args:
        workspace: Source workspace to copy from.
        destination: Destination directory path.

    Raises:
        WorkspaceError: If any workspace operation fails.
    """
    try:
        query = ViewQuery(
            path_pattern="**/*",
            recursive=True,
            include_stats=False,
            include_content=True,
        )
        entries = await workspace.files.query(query)

        for entry in entries:
            content = entry.content
            if content is None:
                continue

            dest_path = destination / entry.path.lstrip("/")
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            if isinstance(content, bytes):
                dest_path.write_bytes(content)
            else:
                dest_path.write_text(str(content), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive guard
        raise WorkspaceError(
            f"Failed to copy workspace to submission: {destination}",
            error_code="WORKSPACE_COPY_FAILED",
            context={"destination": str(destination)},
        ) from exc
