from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from watchfiles import Change

from cairn.cli.commands import CommandType
from cairn.orchestrator.signals import SignalHandler


class StubOrchestrator:
    def __init__(self) -> None:
        self.commands: list[object] = []
        self.fail_dispatch = False

    async def submit_command(self, command: object) -> None:
        if self.fail_dispatch:
            raise RuntimeError("boom")
        self.commands.append(command)


def _write_signal(signals_dir: Path, payload: dict) -> Path:
    path = signals_dir / "queue-test.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_signal_watch_processes_event(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A signal dropped into the directory is claimed, dispatched and removed."""
    orchestrator = StubOrchestrator()
    handler = SignalHandler(tmp_path, orchestrator)
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    signal_file = _write_signal(signals_dir, {"type": "queue", "task": "do work", "priority": 2})

    async def fake_awatch(root: Path, watch_filter=None):
        _ = root, watch_filter
        yield {(Change.added, str(signal_file))}

    monkeypatch.setattr("cairn.orchestrator.signals.awatch", fake_awatch)

    # _watch_events returns once the (mocked) awatch iterator ends.
    await handler._watch_events()

    assert len(orchestrator.commands) == 1
    assert orchestrator.commands[0].type is CommandType.QUEUE
    assert signal_file.exists() is False


@pytest.mark.asyncio
async def test_process_signals_once_handles_sweep(tmp_path: Path) -> None:
    """process_signals_once picks up files awatch might have missed."""
    orchestrator = StubOrchestrator()
    handler = SignalHandler(tmp_path, orchestrator)
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    _write_signal(signals_dir, {"type": "queue", "task": "swept", "priority": 2})

    await handler.process_signals_once()

    assert len(orchestrator.commands) == 1
    assert list(signals_dir.glob("*.json")) == []


@pytest.mark.asyncio
async def test_failed_signal_quarantined_not_deleted(tmp_path: Path) -> None:
    """P1.5: a signal whose dispatch raises lands in signals/failed/ with an
    error sidecar instead of disappearing."""
    orchestrator = StubOrchestrator()
    orchestrator.fail_dispatch = True
    handler = SignalHandler(tmp_path, orchestrator)
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    signal_file = _write_signal(signals_dir, {"type": "queue", "task": "boom", "priority": 2})

    await handler.process_signals_once()

    assert signal_file.exists() is False
    failed = signals_dir / "failed"
    assert (failed / "queue-test.json").exists()
    assert (failed / "queue-test.error.txt").exists()
    assert "boom" in (failed / "queue-test.error.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_concurrent_claim_dispatches_once(tmp_path: Path) -> None:
    """P1.5: two concurrent _process_signal_path calls dispatch exactly once."""
    orchestrator = StubOrchestrator()
    handler = SignalHandler(tmp_path, orchestrator)
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    signal_file = _write_signal(signals_dir, {"type": "queue", "task": "once", "priority": 2})

    await asyncio.gather(
        handler._process_signal_path(signal_file),
        handler._process_signal_path(signal_file),
        handler._process_signal_path(signal_file),
    )

    assert len(orchestrator.commands) == 1
    assert signal_file.exists() is False
    assert list((signals_dir / "failed").glob("*.json")) == []


@pytest.mark.asyncio
async def test_unparseable_signal_quarantined(tmp_path: Path) -> None:
    """An unparseable signal payload is quarantined, not silently dropped."""
    orchestrator = StubOrchestrator()
    handler = SignalHandler(tmp_path, orchestrator)
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    signal_file = signals_dir / "queue-test.json"
    signal_file.write_text("{not json", encoding="utf-8")

    await handler.process_signals_once()

    assert signal_file.exists() is False
    failed = signals_dir / "failed"
    assert (failed / "queue-test.json").exists()
    assert (failed / "queue-test.error.txt").exists()
    assert orchestrator.commands == []
