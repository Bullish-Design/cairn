"""Snapshot wrappers and serialization helpers for resumable Monty execution."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from typing import Any


class MontySnapshot:
    """Wrapper around paused/final Monty execution state."""

    def __init__(
        self,
        state: Any,
        *,
        validate_output: Callable[[Any], Any],
        normalize_exception: Callable[[Exception], Exception],
    ) -> None:
        self._state = state
        self._validate_output = validate_output
        self._normalize_exception = normalize_exception

    @property
    def function_name(self) -> str | None:
        return getattr(self._state, "function_name", None)

    @property
    def args(self) -> tuple[Any, ...] | None:
        value = getattr(self._state, "args", None)
        if value is None:
            return None
        return tuple(value)

    @property
    def kwargs(self) -> dict[str, Any] | None:
        value = getattr(self._state, "kwargs", None)
        if value is None:
            return None
        return dict(value)

    @property
    def final_value(self) -> Any:
        if not self.is_complete:
            raise RuntimeError("Snapshot is paused and has no final output yet")
        return self._validate_output(getattr(self._state, "output"))

    @property
    def output(self) -> Any:
        """Backward-compatible alias for :attr:`final_value`."""
        return self.final_value

    @property
    def is_complete(self) -> bool:
        return hasattr(self._state, "output") and not hasattr(self._state, "function_name")

    def dump(self) -> bytes:
        """Dump paused state to bytes for storage/transit."""
        if self.is_complete:
            raise RuntimeError("Cannot dump a completed snapshot")
        return getattr(self._state, "dump")()

    def resume(self, *args: Any, **kwargs: Any) -> MontySnapshot:
        """Resume paused execution and return the next snapshot/final state."""
        if self.is_complete:
            raise RuntimeError("Cannot resume a completed snapshot")
        try:
            next_state = getattr(self._state, "resume")(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise self._normalize_exception(exc) from exc
        return MontySnapshot(
            next_state,
            validate_output=self._validate_output,
            normalize_exception=self._normalize_exception,
        )


def serialize_snapshot_payload(payload: bytes) -> bytes:
    """Identity helper for API parity when explicit serialization is preferred."""
    return bytes(payload)


def deserialize_snapshot_payload(payload: bytes | bytearray | memoryview) -> bytes:
    """Normalize bytes-like data into immutable ``bytes``."""
    return bytes(payload)


def snapshot_payload_to_base64(payload: bytes) -> str:
    """Encode serialized snapshot payload to URL-safe base64 text."""
    return base64.urlsafe_b64encode(payload).decode("ascii")


def snapshot_payload_from_base64(payload: str) -> bytes:
    """Decode URL-safe base64 text back into serialized snapshot bytes."""
    padded = payload + ("=" * (-len(payload) % 4))
    try:
        return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid base64 snapshot payload") from exc
