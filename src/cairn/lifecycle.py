"""Single canonical lifecycle storage for agent metadata."""

from __future__ import annotations

import time
from pathlib import Path

from fsdantic import Workspace

from cairn.agent import AgentState
from cairn.kv_models import AGENT_KEY_PREFIX, LifecycleRecord


class LifecycleStore:
    """Manages agent lifecycle metadata in workspace KV storage."""

    def __init__(self, workspace: Workspace):
        self.repo = workspace.kv.repository(prefix=AGENT_KEY_PREFIX, model_type=LifecycleRecord)

    async def save(self, record: LifecycleRecord) -> None:
        await self.repo.save(record.agent_id, record)

    async def load(self, agent_id: str) -> LifecycleRecord | None:
        return await self.repo.load(agent_id)

    async def delete(self, agent_id: str) -> None:
        await self.repo.delete(agent_id)

    async def list_all(self) -> list[LifecycleRecord]:
        return await self.repo.list_all()

    async def list_active(self) -> list[LifecycleRecord]:
        all_records = await self.list_all()
        terminal_states = {
            AgentState.ACCEPTED,
            AgentState.REJECTED,
        }
        return [record for record in all_records if record.state not in terminal_states]

    async def cleanup_old(
        self,
        max_age_seconds: float = 86400 * 7,
        agentfs_dir: Path | None = None,
    ) -> int:
        cutoff = time.time() - max_age_seconds
        cleaned = 0

        terminal_states = {
            AgentState.ACCEPTED,
            AgentState.REJECTED,
            AgentState.ERRORED,
        }

        for record in await self.list_all():
            if record.state not in terminal_states:
                continue
            if record.state_changed_at >= cutoff:
                continue

            await self.delete(record.agent_id)
            cleaned += 1

            if agentfs_dir is not None:
                db_path = Path(record.db_path)
                if db_path.exists():
                    db_path.unlink()

        return cleaned
