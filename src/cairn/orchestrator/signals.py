"""Signal polling for orchestrator workflow events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchfiles import Change, awatch

from cairn.cli.commands import CairnCommand, CommandType, parse_command_payload

if TYPE_CHECKING:
    from cairn.orchestrator.orchestrator import CairnOrchestrator

logger = logging.getLogger(__name__)


SIGNAL_SWEEP_INTERVAL_SECONDS = 1.0


def write_signal(cairn_home: Path, command: CairnCommand) -> Path:
    """Atomically drop a command signal file for the daemon to pick up.

    Written to a temp name and renamed so the watcher never observes a
    partially-written file.
    """
    signals_dir = Path(cairn_home) / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    payload = command.to_payload()
    payload["signal_id"] = uuid.uuid4().hex
    payload["issued_at"] = time.time()
    payload["issued_by_pid"] = os.getpid()

    target = signals_dir / f"{command.type.value}-{payload['signal_id']}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


class SignalHandler:
    """Poll signal files and dispatch normalized orchestrator commands."""

    COMPATIBILITY_SIGNAL_TYPES: dict[str, CommandType | str] = {
        "spawn": "spawn",
        "queue": CommandType.QUEUE,
        "accept": CommandType.ACCEPT,
        "reject": CommandType.REJECT,
        "undo": CommandType.UNDO,
    }

    def __init__(
        self,
        cairn_home: Path,
        orchestrator: "CairnOrchestrator",
        *,
        enable_polling: bool = True,
    ):
        self.signals_dir = Path(cairn_home) / "signals"
        self.orchestrator = orchestrator
        self.enable_polling = enable_polling

    async def watch(self) -> None:
        """Watch signal files using filesystem events plus a sweep backstop."""
        if not self.enable_polling:
            return

        self.signals_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(self._watch_events(), self._sweep_loop())

    async def _watch_events(self) -> None:
        try:
            async for changes in awatch(
                self.signals_dir,
                watch_filter=lambda change, path: (
                    str(path).endswith(".json") and "/failed/" not in str(path).replace("\\", "/")
                ),
            ):
                for change_type, path in changes:
                    if change_type in (Change.added, Change.modified):
                        await self._process_signal_path(Path(path))
        except asyncio.CancelledError:
            logger.info("Signal watching cancelled")
            raise
        except Exception as exc:
            logger.exception("Error in signal watcher", extra={"error": str(exc)})
            raise

    async def _sweep_loop(self) -> None:
        """Backstop scan; awatch provides latency, this provides guarantees."""
        while True:
            await self.process_signals_once()
            await asyncio.sleep(SIGNAL_SWEEP_INTERVAL_SECONDS)

    async def process_signals_once(self) -> None:
        """Detect signal files, parse normalized commands, submit, and cleanup."""
        for signal_file in self._detect_signal_files():
            await self._process_signal_path(signal_file)

    async def _process_signal_path(self, signal_file: Path) -> None:
        claimed = signal_file.with_suffix(".processing")
        try:
            signal_file.rename(claimed)      # atomic claim
        except FileNotFoundError:
            return                            # someone else got it
        except OSError as exc:
            logger.warning(
                "Could not claim signal",
                extra={"file": str(signal_file), "error": str(exc)},
            )
            return

        try:
            command = self._parse_signal_file(claimed)
            if command is None:
                self._quarantine(claimed, "unparseable signal payload")
                return
            await self._dispatch(command)
        except Exception as exc:
            logger.exception("Error processing signal", extra={"file": str(claimed)})
            self._quarantine(claimed, str(exc))
        else:
            claimed.unlink(missing_ok=True)

    def _quarantine(self, path: Path, reason: str) -> None:
        """Move a failed signal to signals/failed/ with an error sidecar."""
        failed_dir = self.signals_dir / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        target = failed_dir / path.with_suffix(".json").name
        try:
            path.replace(target)
            target.with_suffix(".error.txt").write_text(reason, encoding="utf-8")
        except OSError:
            logger.exception("Could not quarantine signal", extra={"file": str(path)})

    def _detect_signal_files(self) -> list[Path]:
        return sorted(self.signals_dir.glob("*.json"))

    def _parse_signal_file(self, signal_file: Path) -> CairnCommand | None:
        payload = self._load_payload(signal_file)
        command_type = payload.get("type")

        if not command_type:
            command_type = self._compatibility_command_type(signal_file)

        if command_type is None:
            return None

        self._apply_compatibility_defaults(signal_file, payload, command_type)
        return parse_command_payload(command_type, payload)

    def _compatibility_command_type(self, signal_file: Path) -> CommandType | str | None:
        for prefix, command_type in self.COMPATIBILITY_SIGNAL_TYPES.items():
            if signal_file.stem.startswith(f"{prefix}-"):
                return command_type
        return None

    def _apply_compatibility_defaults(
        self,
        signal_file: Path,
        payload: dict[str, Any],
        command_type: CommandType | str,
    ) -> None:
        normalized_type = command_type.value if isinstance(command_type, CommandType) else command_type

        if normalized_type == CommandType.ACCEPT.value and "agent_id" not in payload:
            payload["agent_id"] = signal_file.stem.replace("accept-", "", 1)
        if normalized_type == CommandType.REJECT.value and "agent_id" not in payload:
            payload["agent_id"] = signal_file.stem.replace("reject-", "", 1)

    async def _dispatch(self, command: CairnCommand) -> None:
        await self.orchestrator.submit_command(command)

    def _load_payload(self, signal_file: Path) -> dict[str, Any]:
        try:
            loaded = json.loads(signal_file.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except FileNotFoundError:
            logger.warning("Signal file missing", extra={"file": str(signal_file)})
            return {}
        except json.JSONDecodeError as exc:
            logger.error(
                "Invalid signal JSON",
                extra={"file": str(signal_file), "error": str(exc)},
            )
            return {}
