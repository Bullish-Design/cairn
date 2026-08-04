"""Agent state management via workspace KV store.

This module provides a high-level interface for agent state persistence
using the workspace's KV store. State is automatically namespaced by
agent ID to prevent collisions between agents.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar

from fsdantic import SerializationError
from fsdantic.exceptions import KeyNotFoundError
from pydantic import BaseModel

if TYPE_CHECKING:
    from fsdantic import Workspace

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Sentinel for missing values
_MISSING = object()


class AgentStateManager:
    """Manage agent state in workspace KV store.

    Provides typed state persistence for agents with automatic
    namespacing by agent ID to prevent collisions.

    Example:
        ```python
        state = AgentStateManager(workspace, "agent-123")

        # Simple key-value
        await state.set("last_file", "/src/main.py")
        path = await state.get("last_file")

        # Typed models
        from pydantic import BaseModel

        class TurnState(BaseModel):
            turn: int
            context: dict

        await state.set_typed("turn_state", TurnState(turn=1, context={}))
        turn_state = await state.get_typed("turn_state", TurnState)

        # Turn tracking
        turn = await state.increment_turn()  # Returns 1, 2, 3, ...
        ```
    """

    def __init__(self, workspace: "Workspace", agent_id: str):
        """Create a state manager for an agent.

        Args:
            workspace: The workspace containing the KV store
            agent_id: Unique identifier for the agent (used for namespacing)
        """
        self._workspace = workspace
        self._agent_id = agent_id
        self._prefix = f"agent:{agent_id}:"

    @property
    def agent_id(self) -> str:
        """The agent ID this manager is scoped to."""
        return self._agent_id

    @property
    def prefix(self) -> str:
        """The KV key prefix for this agent's state."""
        return self._prefix

    @property
    def _kv(self):
        """Access underlying KV manager."""
        return self._workspace.kv

    def _full_key(self, key: str) -> str:
        """Get namespaced key."""
        return self._prefix + key

    async def get(self, key: str, default: Any = None) -> Any:
        """Get state value by key.

        Args:
            key: The state key (will be namespaced automatically)
            default: Value to return if key doesn't exist

        Returns:
            The stored value, or default if not found
        """
        full_key = self._full_key(key)
        try:
            return await self._kv.get(full_key, default=default)
        except KeyNotFoundError:
            return default

    async def set(self, key: str, value: Any) -> None:
        """Set state value by key.

        Args:
            key: The state key (will be namespaced automatically)
            value: The value to store (must be JSON-serializable)
        """
        full_key = self._full_key(key)
        await self._kv.set(full_key, value)

    async def delete(self, key: str) -> bool:
        """Delete state value.

        Args:
            key: The state key to delete

        Returns:
            True if key existed and was deleted, False otherwise
        """
        full_key = self._full_key(key)
        return await self._kv.delete(full_key)

    async def exists(self, key: str) -> bool:
        """Check if state key exists.

        Args:
            key: The state key to check

        Returns:
            True if key exists
        """
        full_key = self._full_key(key)
        try:
            await self._kv.get(full_key)
            return True
        except KeyNotFoundError:
            return False

    async def list_keys(self) -> list[str]:
        """List all state keys for this agent (without the agent prefix)."""
        entries = await self._kv.list(prefix=self._prefix)
        return [entry["key"][len(self._prefix) :] for entry in entries]

    async def get_typed(self, key: str, model: type[T]) -> T | None:
        """Get state as typed Pydantic model.

        Args:
            key: The state key
            model: Pydantic model class to validate against

        Returns:
            Validated model instance, or None if key doesn't exist
        """
        data = await self.get(key)
        if data is None:
            return None
        return model.model_validate(data)

    async def set_typed(self, key: str, value: BaseModel) -> None:
        """Set state as typed Pydantic model.

        Args:
            key: The state key
            value: Pydantic model instance to store
        """
        await self.set(key, value.model_dump(mode="json"))

    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment numeric value and return new value.

        Uses the workspace KV manager's atomic ``increment`` (fsdantic
        >= 0.5.0): the read-modify-write is serialized per key, so concurrent
        increments (e.g. parallel agents advancing a shared turn counter)
        cannot lose updates.

        Args:
            key: The state key (created with value 0 if doesn't exist)
            amount: Amount to increment by (default: 1)

        Returns:
            The new value after incrementing
        """
        full_key = self._full_key(key)
        try:
            return await self._kv.increment(full_key, amount)
        except SerializationError:
            # Non-numeric stored value: reset to 0 then increment, preserving
            # the legacy reset-to-zero behavior while keeping the operation
            # atomic per key.
            await self._kv.set(full_key, 0)
            return await self._kv.increment(full_key, amount)

    async def increment_turn(self) -> int:
        """Increment and return turn counter.

        This is a convenience method for tracking agent turns.

        Returns:
            The new turn number (starts at 1)
        """
        return await self.increment("turn")

    async def get_turn(self) -> int:
        """Get current turn number.

        Returns:
            Current turn number (0 if no turns yet)
        """
        value = await self.get("turn", default=0)
        return int(value) if isinstance(value, (int, float)) else 0

    async def clear_all(self) -> int:
        """Clear all state for this agent.

        Returns:
            Number of keys deleted
        """
        keys = await self.list_keys()
        for key in keys:
            await self.delete(key)
        return len(keys)

    async def touch(self) -> None:
        """Update last_active timestamp."""
        await self.set("last_active", datetime.now(timezone.utc).isoformat())

    async def get_last_active(self) -> datetime | None:
        """Get last_active timestamp.

        Returns:
            datetime of last activity, or None if never set
        """
        value = await self.get("last_active")
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
