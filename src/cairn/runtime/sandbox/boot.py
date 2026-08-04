"""Cairn sandbox bootstrap — executes agent task code inside the bwrap sandbox.

This module is shipped verbatim into the sandbox workspace (``.cairn/boot.py``)
and runs as ``python .cairn/boot.py``. It must be fully self-contained: it can
only use the Python standard library, because inside the sandbox nothing but the
interpreter runtime is mounted.

The sandbox contract is deliberately plain: agent code is a normal Python file
(``.cairn/task.py``) with the following helper functions available as globals:

- ``read_file(path) -> str``
- ``write_file(path, content) -> bool``
- ``list_dir(path=".") -> list[str]``
- ``file_exists(path) -> bool``
- ``delete_file(path) -> bool``
- ``search_files(pattern) -> list[str]``
- ``search_content(pattern, path=".") -> list[dict]``
- ``submit_result(summary, changed_files) -> bool``
- ``log(message) -> bool``

Task inputs (``task_description`` etc.) are injected as globals from
``.cairn/task.json``. A submission is written to ``.cairn/submission.json`` by
``submit_result``; if the task never calls it, an implicit submission is written
with the task description as summary.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import resource
import sys
import time
import traceback
from pathlib import Path

WORKSPACE = Path(os.environ.get("CAIRN_WORKSPACE", "/workspace"))
MAX_FILE_SIZE_BYTES = int(os.environ.get("CAIRN_MAX_FILE_SIZE_BYTES", str(10 * 1024 * 1024)))
MAX_MEMORY_BYTES = int(os.environ.get("CAIRN_MAX_MEMORY_BYTES", "0") or 0)
MAX_CPU_SECONDS = float(os.environ.get("CAIRN_MAX_CPU_SECONDS", "0") or 0)
MAX_RECURSION_DEPTH = int(os.environ.get("CAIRN_MAX_RECURSION_DEPTH", "1000"))
MAX_OUTPUT_FILE_BYTES = int(os.environ.get("CAIRN_MAX_OUTPUT_FILE_BYTES", "0") or 0)
MAX_PROCESSES = int(os.environ.get("CAIRN_MAX_PROCESSES", "0") or 0)
MAX_OPEN_FILES = int(os.environ.get("CAIRN_MAX_OPEN_FILES", "0") or 0)

CAIRN_DIR = WORKSPACE / ".cairn"
TASK_FILE = CAIRN_DIR / "task.py"
TASK_INPUTS_FILE = CAIRN_DIR / "task.json"
SUBMISSION_FILE = CAIRN_DIR / "submission.json"


# ---------------------------------------------------------------------------
# Resource limits (enforced inside the sandbox process)
# ---------------------------------------------------------------------------


def _set_limit(which: int, value: int) -> None:
    """Best-effort rlimit; never lets the limit rise above the inherited hard cap."""
    if value <= 0:
        return
    try:
        soft, hard = resource.getrlimit(which)
    except (ValueError, OSError):
        return
    capped = value if hard == resource.RLIM_INFINITY else min(value, hard)
    try:
        resource.setrlimit(which, (capped, capped))
    except (ValueError, OSError):
        pass


def _apply_resource_limits() -> None:
    """Apply memory/CPU/recursion/file/process limits to the sandbox process.

    Memory is enforced with ``RLIMIT_DATA`` (heap + anonymous mmap) and falls
    back to ``RLIMIT_AS`` when unsupported.  CPU time uses ``RLIMIT_CPU``; the
    host additionally enforces a wall-clock timeout on the subprocess.
    ``RLIMIT_FSIZE`` caps the largest single file, ``RLIMIT_NPROC`` caps the
    process/thread count (fork bombs), and ``RLIMIT_NOFILE`` caps open file
    descriptors.
    """
    if MAX_MEMORY_BYTES > 0:
        for limit_attr in (resource.RLIMIT_DATA, resource.RLIMIT_AS):
            try:
                resource.setrlimit(limit_attr, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
                break
            except (ValueError, OSError):
                continue
    if MAX_CPU_SECONDS > 0:
        try:
            cpu_seconds = int(MAX_CPU_SECONDS)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        except (ValueError, OSError):
            pass
    _set_limit(resource.RLIMIT_FSIZE, MAX_OUTPUT_FILE_BYTES)
    _set_limit(resource.RLIMIT_NPROC, MAX_PROCESSES)
    _set_limit(resource.RLIMIT_NOFILE, MAX_OPEN_FILES)
    sys.setrecursionlimit(MAX_RECURSION_DEPTH)


# ---------------------------------------------------------------------------
# Sandbox API — confined to the workspace directory
# ---------------------------------------------------------------------------


def _resolve(path: str, *, allow_root: bool = False) -> Path:
    """Resolve a sandbox API path to a path inside the workspace.

    Absolute paths and parent traversal are rejected. ``allow_root`` permits the
    workspace root itself (used by list_dir/search_content for "/").
    """
    if allow_root and path == "/":
        return WORKSPACE
    if os.path.isabs(path):
        raise ValueError(f"Absolute paths not allowed in sandbox: {path!r}")
    parts = Path(path).parts
    if ".." in parts:
        raise ValueError(f"Path traversal not allowed in sandbox: {path!r}")
    return (WORKSPACE / path).resolve()


def _relative_files(root: Path) -> list[Path]:
    """All regular files under ``root`` (symlinks excluded), sorted."""
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def read_file(path: str) -> str:
    """Read a text file relative to the workspace root."""
    target = _resolve(path)
    if not target.is_file():
        raise FileNotFoundError(path)
    data = target.read_bytes()
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"File too large: {path} ({len(data)} bytes)")
    return data.decode("utf-8", errors="replace")


def write_file(path: str, content: str) -> bool:
    """Write a text file relative to the workspace root."""
    target = _resolve(path)
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"Content too large: {path} ({len(data)} bytes)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return True


def list_dir(path: str = ".") -> list[str]:
    """List directory entry names relative to the workspace root."""
    target = _resolve(path, allow_root=True)
    if not target.is_dir():
        raise FileNotFoundError(path)
    return sorted(entry.name for entry in target.iterdir())


def file_exists(path: str) -> bool:
    """Check whether a path exists relative to the workspace root."""
    try:
        return _resolve(path).exists()
    except ValueError:
        return False


def delete_file(path: str) -> bool:
    """Delete a file relative to the workspace root (records a tombstone)."""
    target = _resolve(path)
    if not target.exists():
        return False
    if target.is_dir():
        raise ValueError(f"Cannot delete directory: {path}")
    target.unlink()
    return True


def search_files(pattern: str) -> list[str]:
    """List workspace files matching a glob pattern (relative paths)."""
    normalized = pattern.lstrip("/")
    return [
        str(path.relative_to(WORKSPACE))
        for path in _relative_files(WORKSPACE)
        if fnmatch.fnmatch(str(path.relative_to(WORKSPACE)), normalized)
    ]


def _search_targets(path: str) -> list[Path]:
    """Resolve a search_content path scope to file paths."""
    normalized = path.rstrip("/")
    if normalized in {"", ".", "/"}:
        return _relative_files(WORKSPACE)
    if os.path.isabs(path):
        raise ValueError(f"Absolute paths not allowed in sandbox: {path!r}")
    if ".." in Path(path).parts:
        raise ValueError(f"Path traversal not allowed in sandbox: {path!r}")
    candidate = WORKSPACE / normalized
    if candidate.is_dir():
        return _relative_files(candidate)
    return [p for p in WORKSPACE.glob(normalized) if p.is_file() and not p.is_symlink()]


def search_content(pattern: str, path: str = ".") -> list[dict[str, str | int]]:
    """Line-based regex search over workspace files.

    Returns ``[{"file", "line", "text"}, ...]``. Pattern length is capped to
    guard against pathological expressions; CPU time is bounded by RLIMIT_CPU.
    """
    if len(pattern) > 1000:
        raise ValueError("Pattern too long")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc

    matches: list[dict[str, str | int]] = []
    for file_path in _search_targets(path):
        rel = str(file_path.relative_to(WORKSPACE))
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if regex.search(line):
                matches.append({"file": rel, "line": line_number, "text": line})
    return matches


def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Record the agent's submission payload for human review."""
    CAIRN_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": str(summary),
        "changed_files": [str(path) for path in changed_files],
        "submitted_at": time.time(),
    }
    SUBMISSION_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def log(message: str) -> bool:
    """Append a message to the sandbox stdout log."""
    print(message, flush=True)
    return True


def _api_namespace(inputs: dict[str, object]) -> dict[str, object]:
    """Build the global namespace in which task code executes."""
    namespace: dict[str, object] = dict(inputs)
    namespace["inputs"] = inputs
    namespace.update(
        {
            "read_file": read_file,
            "write_file": write_file,
            "list_dir": list_dir,
            "file_exists": file_exists,
            "delete_file": delete_file,
            "search_files": search_files,
            "search_content": search_content,
            "submit_result": submit_result,
            "log": log,
        }
    )
    namespace["__name__"] = "__cairn_task__"
    namespace["__file__"] = str(TASK_FILE)
    return namespace


def _run_task() -> int:
    _apply_resource_limits()
    # Raw filesystem operations in task code resolve relative to the workspace.
    os.chdir(WORKSPACE)

    task_code = TASK_FILE.read_text(encoding="utf-8")
    inputs: dict[str, object] = {}
    if TASK_INPUTS_FILE.exists():
        try:
            loaded = json.loads(TASK_INPUTS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                inputs = loaded
        except (OSError, ValueError):
            inputs = {}

    namespace = _api_namespace(inputs)
    code = compile(task_code, str(TASK_FILE), "exec")
    exec(code, namespace)  # noqa: S102 — task code execution is the sandbox's purpose

    if not SUBMISSION_FILE.exists():
        summary = str(inputs.get("task_description") or "completed")
        submit_result(summary, [])
    return 0


def main() -> int:
    """Run the task, returning a process exit code."""
    try:
        return _run_task()
    except SystemExit as exc:  # task called sys.exit(...)
        code = exc.code
        if code is None:
            return 0
        return int(code) if isinstance(code, int) else 1
    except Exception:  # noqa: BLE001 — traceback to stderr is the error channel
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
