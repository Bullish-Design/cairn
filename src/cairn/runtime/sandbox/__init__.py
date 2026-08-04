"""bwrap-backed sandbox execution for agent code.

Public API:
- :class:`BwrapExecutor` — materialize → sandbox run → re-import changeset.
- :class:`SandboxResult` — outcome of a sandboxed execution.
- :class:`SandboxExecutionError` — sandbox launch/execution failures.

The bootstrap script that runs inside the sandbox lives in ``cairn.runtime.
sandbox.boot``; it is shipped verbatim into the workspace as ``.cairn/boot.py``.
"""

from __future__ import annotations

from typing import Protocol

from cairn.runtime.sandbox.sandbox import (
    BwrapExecutor,
    SANDBOX_DIR_NAME,
    SandboxExecutionError,
    SandboxResult,
)


class SandboxExecutor(Protocol):
    """Protocol for sandbox executors (implemented by BwrapExecutor)."""

    async def run(self, *, code: str, task: str) -> SandboxResult:
        """Run agent code in the sandbox and return the result."""
        ...


__all__ = [
    "BwrapExecutor",
    "SANDBOX_DIR_NAME",
    "SandboxExecutionError",
    "SandboxExecutor",
    "SandboxResult",
]
