"""Daemon liveness tracking via a pidfile under $CAIRN_HOME/state."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PIDFILE_NAME = "orchestrator.pid"


def pidfile_path(cairn_home: Path) -> Path:
    return Path(cairn_home) / "state" / PIDFILE_NAME


def read_daemon_pid(cairn_home: Path) -> int | None:
    """Return the live daemon's pid, or None if no daemon is running.

    A stale pidfile (process gone) is treated as no daemon.
    """
    path = pidfile_path(cairn_home)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        os.kill(pid, 0)          # signal 0 = liveness probe only
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid               # exists, owned by another user
    return pid


@contextmanager
def daemon_pidfile(cairn_home: Path) -> Iterator[Path]:
    """Claim the daemon pidfile for the duration of the block.

    Raises RuntimeError if another live daemon already holds it.
    """
    path = pidfile_path(cairn_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_daemon_pid(cairn_home)
    if existing is not None and existing != os.getpid():
        raise RuntimeError(f"A Cairn daemon is already running (pid {existing})")
    tmp = path.with_suffix(".pid.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    tmp.replace(path)            # atomic
    try:
        yield path
    finally:
        try:
            if read_daemon_pid(cairn_home) == os.getpid():
                path.unlink(missing_ok=True)
        except OSError:
            pass
