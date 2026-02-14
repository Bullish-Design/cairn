"""Single canonical lifecycle storage for agent metadata.

This module provides the unified storage layer for agent lifecycle metadata,
replacing the previous scattered approach across runtime memory, state files,
and bin DB metadata.

Design Principles:
- Single source of truth: bin.db KV namespace
- Linear and idempotent operations
- Clear recovery path from persistent state
"""

from __future__ import annotations

import time
from pathlib import Path

from agentfs_sdk import AgentFS

from cairn.agent import AgentState
from cairn.kv_models import LifecycleRecord
from cairn.kv_store import KVRepository


class LifecycleStore:
    """Manages agent lifecycle metadata in AgentFS KV namespace.

    This is the single source of truth for agent state. All lifecycle
    transitions, completions, and cleanup operations go through this store.
    """

    def __init__(self, storage: AgentFS):
        self.repo = KVRepository(storage)

    async def save(self, record: LifecycleRecord) -> None:
        """Save or update an agent lifecycle record.

        This is the canonical write operation for agent state.
        All state transitions must call this method.
        """
        await self.repo.save_lifecycle(record)

    async def load(self, agent_id: str) -> LifecycleRecord | None:
        """Load an agent lifecycle record by ID."""
        return await self.repo.load_lifecycle(agent_id)

    async def delete(self, agent_id: str) -> None:
        """Delete an agent lifecycle record."""
        await self.repo.delete_lifecycle(agent_id)

    async def list_all(self) -> list[LifecycleRecord]:
        """List all agent lifecycle records."""
        return await self.repo.list_lifecycle()

    async def list_active(self) -> list[LifecycleRecord]:
        """List only active (non-terminal) agent records."""
        all_records = await self.list_all()
        terminal_states = {
            AgentState.ACCEPTED,
            AgentState.REJECTED,
        }
        return [r for r in all_records if r.state not in terminal_states]

    async def cleanup_old(
        self,
        max_age_seconds: float = 86400 * 7,
        agentfs_dir: Path | None = None,
    ) -> int:
        """Remove lifecycle records and DBs for old completed agents.

        This is the single retention policy for the system.
        """
        cutoff = time.time() - max_age_seconds
        all_records = await self.list_all()
        cleaned = 0

        terminal_states = {
            AgentState.ACCEPTED,
            AgentState.REJECTED,
            AgentState.ERRORED,
        }

        for record in all_records:
            if record.state not in terminal_states:
                continue

            if record.state_changed_at >= cutoff:
                continue

            await self.delete(record.agent_id)
            cleaned += 1

            if agentfs_dir:
                db_path = Path(record.db_path)
                if db_path.exists():
                    db_path.unlink()

        return cleaned
