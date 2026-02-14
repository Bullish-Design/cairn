"""Signal polling for orchestrator workflow events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cairn.commands import CairnCommand, CommandType, parse_command_payload

if TYPE_CHECKING:
    from cairn.orchestrator import CairnOrchestrator


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
        """Poll for signal files every 500ms."""
        if not self.enable_polling:
            return

        self.signals_dir.mkdir(parents=True, exist_ok=True)

        while True:
            await asyncio.sleep(0.5)
            await self.process_signals_once()

    async def process_signals_once(self) -> None:
        """Detect signal files, parse normalized commands, submit, and cleanup."""
        for signal_file in self._detect_signal_files():
            try:
                command = self._parse_signal_file(signal_file)
                if command is None:
                    continue
                await self._dispatch(command)
            finally:
                signal_file.unlink(missing_ok=True)

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
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
