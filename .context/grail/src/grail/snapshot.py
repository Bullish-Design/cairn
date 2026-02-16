"""Snapshot wrapper for pause/resume execution.

Snapshots capture the execution state of a GrailScript program so it can pause
at external function boundaries and later resume where it left off. External
calls may be synchronous (a direct return value) or asynchronous (Monty expects
an intermediate future). For async externals, the snapshot resume protocol
creates a future with a call ID, then resolves it with the final value.

Note: serialized snapshots do NOT include the `SourceMap` or external function
registry; you must retain those from the original execution context in order to
load and resume a snapshot.
"""

import asyncio
import inspect

from typing import Any, Callable

try:
    import pydantic_monty
except ImportError:
    pydantic_monty = None

from grail._types import SourceMap


class Snapshot:
    """
    Wrapper around Monty's snapshot for pause/resume execution.

    Allows inspecting external function calls and resuming with results.
    """

    def __init__(self, monty_snapshot: Any, source_map: SourceMap, externals: dict[str, Callable]):
        """
        Initialize snapshot wrapper.

        Args:
            monty_snapshot: Underlying Monty snapshot
            source_map: Line number mapping
            externals: External function implementations
        """
        self._monty_snapshot = monty_snapshot
        self._source_map = source_map
        self._externals = externals

    @property
    def function_name(self) -> str:
        """Name of the external function being called."""
        return self._monty_snapshot.function_name

    @property
    def args(self) -> tuple[Any, ...]:
        """Positional arguments for the function call."""
        return self._monty_snapshot.args

    @property
    def kwargs(self) -> dict[str, Any]:
        """Keyword arguments for the function call."""
        return self._monty_snapshot.kwargs

    @property
    def call_id(self) -> int:
        """Unique identifier for this external call."""
        return getattr(self._monty_snapshot, "call_id", 0)

    @property
    def value(self) -> Any:
        """
        Final result value (only available when is_complete=True).

        Returns:
            Final script result

        Raises:
            RuntimeError: If execution not complete
        """
        if not self.is_complete:
            raise RuntimeError("Execution not complete")
        return self._monty_snapshot.output

    def resume(
        self, return_value: Any = None, exception: BaseException | None = None
    ) -> "Snapshot":
        """Resume execution after an external function call.

        If the external is async, Monty expects a future-style resume (call ID
        first, then final value). Sync externals resume with the return value
        directly.

        Args:
            return_value: Value to return from external function
            exception: Exception to raise in Monty

        Returns:
            Snapshot representing the next pause point or completion.
        """

        if exception is not None:
            next_snapshot = self._monty_snapshot.resume(exception=exception)
        else:
            # Check if the current external function is async
            func_name = self._monty_snapshot.function_name
            if func_name in self._externals:
                external_func = self._externals[func_name]
                if asyncio.iscoroutinefunction(external_func) or inspect.isasyncgenfunction(
                    external_func
                ):
                    # Async external function protocol:
                    # 1. Monty pauses at an external call, providing a call_id.
                    # 2. We call the async external function ourselves.
                    # 3. We create a "future" resume with the call_id.
                    # 4. We resolve the future with the actual return value.
                    # This two-step resume is required because Monty's async model
                    # uses futures to represent pending async operations.
                    call_id = self._monty_snapshot.call_id

                    future_snapshot = self._monty_snapshot.resume(future=...)

                    next_snapshot = future_snapshot.resume(
                        {call_id: {"return_value": return_value}}
                    )
                else:
                    # Sync external functions can use direct return value
                    next_snapshot = self._monty_snapshot.resume(return_value=return_value)
            else:
                # External function not found in registry, try direct resume
                next_snapshot = self._monty_snapshot.resume(return_value=return_value)

        return Snapshot(next_snapshot, self._source_map, self._externals)

    def dump(self) -> bytes:
        """Serialize the current snapshot to bytes.

        Returns:
            Serialized snapshot data. This does not include the source map or
            externals registry; those must be supplied when loading.
        """
        return self._monty_snapshot.dump()

    @staticmethod
    def load(data: bytes, source_map: SourceMap, externals: dict[str, Callable]) -> "Snapshot":
        """Deserialize a snapshot from bytes.

        Note: source_map and externals are NOT included in the serialized data
        and must be provided from the original GrailScript context. This means
        you must retain access to the original script to restore a snapshot.

        Args:
            data: Serialized snapshot data
            source_map: Line number mapping
            externals: External function implementations

        Returns:
            Restored Snapshot instance
        """
        if pydantic_monty is None:
            raise RuntimeError("pydantic-monty not installed")

        monty_snapshot = pydantic_monty.MontySnapshot.load(data)
        return Snapshot(monty_snapshot, source_map, externals)

    @property
    def is_complete(self) -> bool:
        """
        Check if execution is complete.

        Returns:
            True if execution finished, False if paused
        """

        if pydantic_monty is None:
            return False

        return isinstance(self._monty_snapshot, pydantic_monty.MontyComplete)
