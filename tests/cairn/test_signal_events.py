from __future__ import annotations

import json
from pathlib import Path

import pytest
from watchfiles import Change

from cairn.cli.commands import CommandType
from cairn.orchestrator.signals import SignalHandler


class StubOrchestrator:
    def __init__(self) -> None:
        self.commands: list[object] = []

    async def submit_command(self, command: object) -> None:
        self.commands.append(command)


@pytest.mark.asyncio
async def test_signal_watch_processes_event(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orchestrator = StubOrchestrator()
    handler = SignalHandler(tmp_path, orchestrator)
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    signal_file = signals_dir / "queue-test.json"

    async def fake_awatch(root: Path, watch_filter=None):
        assert root == signals_dir
        _ = watch_filter
        signal_file.write_text(
            json.dumps({"type": "queue", "task": "do work", "priority": 2}),
            encoding="utf-8",
        )
        yield {(Change.added, str(signal_file))}

    monkeypatch.setattr("cairn.orchestrator.signals.awatch", fake_awatch)

    await handler.watch()

    assert len(orchestrator.commands) == 1
    assert orchestrator.commands[0].type is CommandType.QUEUE
    assert signal_file.exists() is False
