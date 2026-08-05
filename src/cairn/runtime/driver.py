"""Iterative agent driver interface (review §4.3).

A proper coding agent iterates: *model → inspect → edit → run tests → inspect
failure → edit → submit*.  This module defines the contract:

- :class:`WorkspaceCapability` — the narrow capability a driver (or its model
  client) receives.  It can read/list/search and write/delete *within the
  bounded workspace only*, and execute commands through the sandbox runner —
  never raw database access, never host paths.
- :class:`IterativeDriver` — the protocol a driver implements.
- :class:`ScriptedDriver` — a reference driver that executes an explicit
  step plan with a hard step limit (used by tests and as an embeddable
  scripted agent).
- :class:`ProjectView` — the read-only snapshot interface code providers
  receive in their context (replaces the raw writable workspace, review
  §3.5): read/list/search/stat over the canonical tree, gitignore-aware and
  no-follow.

Drivers that run inside the sandbox use the sandbox API (``read_file``,
``write_file``, ...) plus ordinary subprocess execution for tests — the
capability class here is the *host-side / embedding* view, and it routes
``run`` through the sandbox runner so no host command ever executes.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from cairn.core.exceptions import SecurityError
from cairn.runtime.repo import ProjectFilter

SandboxRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]


def _confine(root: Path, rel: str) -> Path:
    """Resolve ``rel`` beneath ``root``; absolute paths and ``..`` traversal
    are rejected (the narrow capability never touches host paths)."""
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise SecurityError(
            f"path escapes the workspace: {rel!r}",
            error_code="PATH_ESCAPE",
            context={"path": rel},
        )
    target = (root / rel).resolve()
    if not target.is_relative_to(root.resolve()):
        raise SecurityError(
            f"path escapes the workspace: {rel!r}",
            error_code="PATH_ESCAPE",
            context={"path": rel},
        )
    return target


class WorkspaceCapability:
    """Narrow, path-validated capability over one bounded workspace root.

    ``run`` executes through a provided sandbox runner (or the in-sandbox
    subprocess path); it is a no-op when no runner is configured.
    """

    def __init__(self, root: Path | str, runner: SandboxRunner | None = None) -> None:
        self.root = Path(root).resolve()
        self.runner = runner

    async def read(self, rel: str) -> str:
        target = _confine(self.root, rel)
        return target.read_text(encoding="utf-8")

    async def list_dir(self, rel: str = ".") -> list[str]:
        target = _confine(self.root, rel)
        if not target.is_dir():
            return []
        return sorted(entry.name for entry in os.scandir(target))

    async def search(self, pattern: str) -> list[str]:
        normalized = pattern.lstrip("./")
        matches: list[str] = []
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if path.is_symlink() or not path.is_file():
                continue
            if fnmatch.fnmatch(rel, normalized):
                matches.append(rel)
        return matches

    async def write(self, rel: str, content: str) -> None:
        target = _confine(self.root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def delete(self, rel: str) -> bool:
        target = _confine(self.root, rel)
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
                return True
        except OSError:
            return False
        return False

    async def run(self, command: Sequence[str], *, timeout: float = 30.0) -> tuple[int, str]:
        """Execute a command through the sandbox runner (never on the host)."""
        if self.runner is None:
            raise SecurityError(
                "no sandbox runner configured for this capability",
                error_code="NO_SANDBOX_RUNNER",
            )
        return await self.runner(list(command))

    async def run_tests(self, *, timeout: float = 60.0) -> tuple[int, str]:
        """Best-effort test invocation (pytest when present, else nothing)."""
        import shutil

        if shutil.which("pytest"):
            return await self.run(["pytest", "-q"], timeout=timeout)
        return 0, "no pytest available"


class IterativeDriver(Protocol):
    """A driver iterates model → inspect → edit → test → submit."""

    async def run(self, task: str, capability: WorkspaceCapability, *, step_limit: int = 50) -> dict:
        """Execute the task against the capability; return the submission
        payload (``summary`` + ``changed_files``)."""
        ...


@dataclass
class DriverStep:
    """One scripted driver step."""

    action: str  # read | write | delete | run | submit
    path: str = ""
    content: str = ""
    command: list[str] = field(default_factory=list)


class ScriptedDriver:
    """Reference driver: executes an explicit step plan with a hard limit.

    Steps: ``read <path>``, ``write <path> <content>``, ``delete <path>``,
    ``run <cmd...>``, ``submit <summary> [changed_files...]``.
    """

    def __init__(self, steps: Sequence[DriverStep]) -> None:
        self.steps = list(steps)

    async def run(self, task: str, capability: WorkspaceCapability, *, step_limit: int = 50) -> dict:
        summary = task
        changed: list[str] = []
        for index, step in enumerate(self.steps[:step_limit]):
            if step.action == "read":
                await capability.read(step.path)
            elif step.action == "write":
                await capability.write(step.path, step.content)
                if step.path not in changed:
                    changed.append(step.path)
            elif step.action == "delete":
                if await capability.delete(step.path):
                    changed.append(step.path)
            elif step.action == "run":
                await capability.run(step.command)
            elif step.action == "submit":
                summary = step.content or summary
                if step.path:
                    changed.append(step.path)
            else:
                raise ValueError(f"unknown driver step: {step.action}")
        return {"summary": summary, "changed_files": sorted(set(changed))}


class ProjectView:
    """Read-only snapshot interface for code providers (review §3.5).

    Replaces the raw writable workspace in the provider context: providers
    can inspect the canonical tree (gitignore-aware, no symlink following)
    but can never mutate it or the metadata databases.
    """

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self._filter = ProjectFilter(self.project_root)

    async def read(self, rel: str) -> str:
        target = _confine(self.project_root, rel)
        return target.read_text(encoding="utf-8")

    async def list_dir(self, rel: str = ".") -> list[str]:
        target = _confine(self.project_root, rel)
        if not target.is_dir():
            return []
        return sorted(entry.name for entry in os.scandir(target) if self._filter.allows(Path(entry.path)))

    async def search(self, pattern: str) -> list[str]:
        normalized = pattern.lstrip("./")
        matches: list[str] = []
        for path in sorted(self.project_root.rglob("*")):
            if path.is_symlink():
                continue
            rel = path.relative_to(self.project_root).as_posix()
            if not self._filter.allows(path):
                continue
            if path.is_file() and fnmatch.fnmatch(rel, normalized):
                matches.append(rel)
        return matches

    async def stat(self, rel: str) -> dict:
        target = _confine(self.project_root, rel)
        st = target.lstat()
        return {
            "path": rel,
            "kind": "dir" if stat.S_ISDIR(st.st_mode) else ("symlink" if stat.S_ISLNK(st.st_mode) else "file"),
            "size": st.st_size if stat.S_ISREG(st.st_mode) else None,
        }
