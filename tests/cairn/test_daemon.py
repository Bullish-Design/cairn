from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cairn.orchestrator.daemon import daemon_pidfile, pidfile_path, read_daemon_pid


def test_pidfile_path_under_state(tmp_path: Path) -> None:
    assert pidfile_path(tmp_path) == tmp_path / "state" / "orchestrator.pid"


def test_read_daemon_pid_absent(tmp_path: Path) -> None:
    assert read_daemon_pid(tmp_path) is None


def test_read_daemon_pid_dead_process(tmp_path: Path) -> None:
    """A pidfile naming a dead pid is treated as absent."""
    path = pidfile_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 2**22}), encoding="utf-8")  # implausibly large -> no such process
    assert read_daemon_pid(tmp_path) is None


def test_daemon_pidfile_roundtrip(tmp_path: Path) -> None:
    with daemon_pidfile(tmp_path) as path:
        assert path.exists()
        assert read_daemon_pid(tmp_path) == os.getpid()
    assert path.exists() is False
    assert read_daemon_pid(tmp_path) is None


def test_daemon_pidfile_second_claim_raises(tmp_path: Path) -> None:
    """A pidfile held by another live process raises."""
    path = pidfile_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # os.getppid() is a live process (our parent) that is not us.
    path.write_text(json.dumps({"pid": os.getppid()}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="already running"):
        with daemon_pidfile(tmp_path):
            pytest.fail("should not be reachable")
    # After the foreign pidfile is removed, claiming works again.
    path.unlink()
    with daemon_pidfile(tmp_path):
        pass
