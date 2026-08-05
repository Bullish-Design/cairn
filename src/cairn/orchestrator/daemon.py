"""Daemon liveness tracking for the CLI.

The daemon's control socket (``$CAIRN_HOME/state/orchestrator.sock``) is the
ownership primitive (review §3.7): a second daemon cannot bind a live socket,
and the CLI probes liveness by connecting.  The pidfile is kept as
informational metadata only (human debugging, ``cairn status``) — it is
neither claimed nor trusted for ownership, so PID reuse and check-then-replace
races cannot mislead.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cairn.orchestrator.transport import daemon_running, socket_path

PIDFILE_NAME = "orchestrator.pid"


def pidfile_path(cairn_home: Path) -> Path:
    return Path(cairn_home) / "state" / PIDFILE_NAME


def read_daemon_pid(cairn_home: Path) -> int | None:
    """Return the pid recorded by the running daemon, or None.

    Informational only: ownership is the control socket.  A missing/stale
    pidfile is treated as no daemon, but a live socket always wins (the
    daemon may be mid-startup before it writes the pidfile).
    """
    if daemon_running(cairn_home):
        path = pidfile_path(cairn_home)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return int(payload["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
    return None


def write_daemon_pid(cairn_home: Path) -> None:
    """Record this process as the daemon (informational)."""
    path = pidfile_path(cairn_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".pid.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    tmp.replace(path)


def remove_daemon_pid(cairn_home: Path) -> None:
    try:
        pidfile_path(cairn_home).unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "PIDFILE_NAME",
    "pidfile_path",
    "read_daemon_pid",
    "remove_daemon_pid",
    "socket_path",
    "write_daemon_pid",
]
