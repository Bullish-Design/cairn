"""Signal polling for orchestrator workflow events."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchfiles import Change, awatch

from cairn.cli.commands import CairnCommand, CommandType, parse_command_payload

if TYPE_CHECKING:
    from cairn.orchestrator.orchestrator import CairnOrchestrator

logger = logging.getLogger(__name__)


class SignalHandler:
    """Poll signal files and dispatch normalized orchestrator commands."""

    COMPATIBILITY_SIGNAL_TYPES: dict[str, CommandType | str] = {
        "spawn": "spawn",
        "queue": CommandType.QUEUE,
        "accept": CommandType.ACCEPT,
        "reject": CommandType.REJECT,
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
        """Watch signal files using filesystem events."""
        if not self.enable_polling:
            return

        self.signals_dir.mkdir(parents=True, exist_ok=True)
        await self.process_signals_once()

        try:
            async for changes in awatch(
                self.signals_dir,
                watch_filter=lambda change, path: str(path).endswith(".json"),
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

    async def process_signals_once(self) -> None:
        """Detect signal files, parse normalized commands, submit, and cleanup."""
        for signal_file in self._detect_signal_files():
            await self._process_signal_path(signal_file)

    async def _process_signal_path(self, signal_file: Path) -> None:
        try:
            command = self._parse_signal_file(signal_file)
            if command is None:
                return
            await self._dispatch(command)
        except Exception as exc:
            logger.exception(
                "Error processing signal",
                extra={"file": str(signal_file), "error": str(exc)},
            )
        finally:
            try:
                signal_file.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(
                    "Failed to remove signal file",
                    extra={"file": str(signal_file), "error": str(exc)},
                )

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
