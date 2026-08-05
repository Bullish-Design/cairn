"""Durable request/result transport for the daemon ↔ CLI boundary.

Replaces the signal-file transport (review §3.1): a Unix-domain socket with
a persisted command table gives command IDs, idempotent dispatch, in-flight
recovery, and direct synchronous feedback — an accept either returns its
result or an error, never a five-minute stale poll.

Contract:
- The daemon binds ``$CAIRN_HOME/state/orchestrator.sock``.  The socket bind
  *is* the daemon-ownership primitive (review §3.7): a second daemon cannot
  bind a live socket; a stale socket file (crash left it behind) is detected
  by a failed connect probe and unlinked before rebinding.
- Each request carries a client-generated ``command_id``.  The daemon records
  every dispatch in ``bin.db`` (``CommandRecord``): a retry with the same id
  returns the recorded result/error instead of re-executing; a "pending"
  record on startup was in flight when the daemon died and recovery fails it.
- Requests are single JSON objects, one per connection; the daemon responds
  with a single JSON object carrying the result (``CommandResult`` payload)
  or the error.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from cairn.cli.commands import CairnCommand
from cairn.core.exceptions import CairnError
from cairn.orchestrator.lifecycle import COMMAND_KEY_PREFIX, CommandRecord

logger = logging.getLogger(__name__)

SOCKET_NAME = "orchestrator.sock"

RequestHandler = Callable[[CairnCommand], Awaitable[object]]


class TransportRequest(BaseModel):
    """One CLI→daemon command request over the socket."""

    command_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    issued_at: float = Field(default_factory=time.time)


class TransportResponse(BaseModel):
    """The daemon's reply: the result payload, or the error text."""

    command_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


def socket_path(cairn_home: Path) -> Path:
    return Path(cairn_home) / "state" / SOCKET_NAME


def daemon_running(cairn_home: Path) -> bool:
    """True if a daemon is serving on the control socket (connect probe)."""
    path = socket_path(cairn_home)
    if not path.exists():
        return False
    return _can_connect(path)


def _can_connect(path: Path) -> bool:
    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        sock.close()


class CommandTable:
    """Persisted idempotency + result table for transport commands."""

    def __init__(self, workspace) -> None:
        self._repo = workspace.kv.repository(prefix=COMMAND_KEY_PREFIX, model_type=CommandRecord)

    async def begin(self, command_id: str, command_type: str) -> bool:
        """Create a pending record; False if the command was already handled
        (idempotent replay — the caller should return the recorded result)."""
        existing = await self.load(command_id)
        if existing is not None:
            return False
        await self._repo.save(
            command_id,
            CommandRecord(
                command_id=command_id,
                command_type=command_type,
                status="pending",
                created_at=time.time(),
                updated_at=time.time(),
            ),
        )
        return True

    async def load(self, command_id: str) -> CommandRecord | None:
        return await self._repo.load(command_id)

    async def complete(self, command_id: str, result: dict[str, Any]) -> None:
        await self._repo.save(
            command_id,
            CommandRecord(
                command_id=command_id,
                command_type="",
                status="done",
                result=result,
                created_at=time.time(),
                completed_at=time.time(),
                updated_at=time.time(),
            ),
        )

    async def fail(self, command_id: str, error: str) -> None:
        await self._repo.save(
            command_id,
            CommandRecord(
                command_id=command_id,
                command_type="",
                status="failed",
                error=error,
                created_at=time.time(),
                completed_at=time.time(),
                updated_at=time.time(),
            ),
        )

    async def list_pending(self) -> list[CommandRecord]:
        records = await self._repo.list_all()
        return [r for r in records if r.status == "pending"]

    async def recover_inflight(self) -> int:
        """Fail every pending command (the daemon died mid-dispatch)."""
        failed = 0
        for record in await self.list_pending():
            await self.fail(record.command_id, "Interrupted by orchestrator restart")
            failed += 1
        if failed:
            logger.warning("Recovered in-flight transport commands", extra={"count": failed})
        return failed


class OrchestratorTransport:
    """Unix-socket server side: accepts requests, dispatches idempotently."""

    def __init__(
        self,
        socket_path: Path,
        handler: RequestHandler,
        command_table: CommandTable,
    ) -> None:
        self.path = socket_path
        self.handler = handler
        self.command_table = command_table
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            # Stale socket from a crashed daemon (a live one would have
            # answered the connect probe and start_unix_server would fail).
            if daemon_running(self.path.parent.parent):
                raise RuntimeError("A Cairn daemon is already running")
            self.path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle_connection, path=str(self.path))
        logger.info("Orchestrator transport listening", extra={"socket": str(self.path)})

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("Transport not started")
        await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.path.unlink(missing_ok=True)

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await asyncio.wait_for(reader.read(1 << 20), timeout=30.0)
            if not data:
                return
            try:
                request = TransportRequest.model_validate_json(data)
            except Exception as exc:  # noqa: BLE001 - malformed request
                await _write_response(writer, TransportResponse(command_id="", ok=False, error=f"bad request: {exc}"))
                return
            response = await self._dispatch(request)
            await _write_response(writer, response)
        except TimeoutError:
            logger.warning("Transport client timed out while sending")
        except Exception as exc:
            logger.exception("Transport connection handler failed", extra={"error": str(exc)})
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, request: TransportRequest) -> TransportResponse:
        handled = await self.command_table.begin(request.command_id, request.type)
        if not handled:
            # Idempotent replay: return the recorded outcome.
            record = await self.command_table.load(request.command_id)
            if record is None:
                return TransportResponse(command_id=request.command_id, ok=False, error="unknown command id")
            return TransportResponse(
                command_id=request.command_id,
                ok=record.status == "done",
                result=record.result,
                error=record.error,
            )
        try:
            command = _parse_command(request)
            result = await self.handler(command)
        except Exception as exc:  # noqa: BLE001 - errors travel back to the client
            message = str(exc)
            if isinstance(exc, CairnError) and exc.error_code:
                message = f"{exc.error_code}: {exc}"
            await self.command_table.fail(request.command_id, message)
            return TransportResponse(command_id=request.command_id, ok=False, error=message)
        await self.command_table.complete(request.command_id, _normalize_result(result))
        return TransportResponse(command_id=request.command_id, ok=True, result=_normalize_result(result))


def _normalize_result(result: object) -> dict[str, Any]:
    """CommandResult (dataclass) -> plain dict for the response payload."""
    from typing import cast

    from cairn.cli.commands import CommandResult

    if isinstance(result, CommandResult):
        return {"agent_id": result.agent_id, **result.payload}
    if isinstance(result, dict):
        return cast(dict[str, Any], result)
    return {"result": result}


async def _write_response(writer: asyncio.StreamWriter, response: TransportResponse) -> None:
    writer.write((response.model_dump_json() + "\n").encode("utf-8"))
    await writer.drain()


def _parse_command(request: TransportRequest) -> CairnCommand:
    from cairn.cli.commands import parse_command_payload

    return parse_command_payload(request.type, request.payload)


async def send_request(cairn_home: Path, command: CairnCommand, *, timeout: float = 60.0) -> TransportResponse:
    """CLI side: send one command over the socket and await the result.

    Raises ``ConnectionError`` when no daemon is listening.
    """

    path = socket_path(cairn_home)
    if not path.exists():
        raise ConnectionError(f"No Cairn daemon is running (no socket at {path})")

    reader, writer = await asyncio.open_unix_connection(str(path))
    request = TransportRequest(
        command_id=uuid.uuid4().hex,
        type=command.type.value,
        payload=command.to_payload(),
    )
    try:
        writer.write((request.model_dump_json() + "\n").encode("utf-8"))
        await writer.drain()
        data = await asyncio.wait_for(reader.read(1 << 20), timeout=timeout)
        if not data:
            raise ConnectionError("daemon closed the connection without a response")
        return TransportResponse.model_validate_json(data)
    except TimeoutError as exc:
        raise TimeoutError(f"daemon did not respond within {timeout}s") from exc
    except OSError as exc:
        raise ConnectionError(f"cannot reach Cairn daemon: {exc}") from exc
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


__all__ = [
    "SOCKET_NAME",
    "CommandTable",
    "OrchestratorTransport",
    "TransportRequest",
    "TransportResponse",
    "daemon_running",
    "send_request",
    "socket_path",
]
