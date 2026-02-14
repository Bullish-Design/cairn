"""Repository helpers for typed KV reads/writes."""

from __future__ import annotations

import json
from typing import Any

from agentfs_sdk import AgentFS

from cairn.kv_models import AGENT_KEY_PREFIX, SUBMISSION_KEY, LifecycleRecord, SubmissionRecord, agent_key


class KVRepository:
    """Typed adapter around AgentFS KV APIs."""

    def __init__(self, storage: AgentFS):
        self.storage = storage

    async def save_lifecycle(self, record: LifecycleRecord) -> None:
        await self.storage.kv.set(agent_key(record.agent_id), record.model_dump_json())

    async def load_lifecycle(self, agent_id: str) -> LifecycleRecord | None:
        data = await self.storage.kv.get(agent_key(agent_id))
        if data is None:
            return None
        return LifecycleRecord.model_validate_json(data)

    async def delete_lifecycle(self, agent_id: str) -> None:
        await self.storage.kv.delete(agent_key(agent_id))

    async def list_lifecycle(self) -> list[LifecycleRecord]:
        keys = await self.storage.kv.list()
        records: list[LifecycleRecord] = []
        for key in keys:
            if not key.startswith(AGENT_KEY_PREFIX):
                continue
            data = await self.storage.kv.get(key)
            if data:
                records.append(LifecycleRecord.model_validate_json(data))
        return records

    async def save_submission(self, agent_id: str, submission: dict[str, Any]) -> None:
        record = SubmissionRecord(agent_id=agent_id, submission=submission)
        await self.storage.kv.set(SUBMISSION_KEY, record.model_dump_json())

    async def load_submission(self, agent_id: str) -> dict[str, Any] | None:
        data = await self.storage.kv.get(SUBMISSION_KEY)
        if not data:
            return None

        payload = json.loads(data)

        # New typed payload format.
        if isinstance(payload, dict) and "submission" in payload:
            return SubmissionRecord.model_validate(payload).submission

        # Backward compatibility: previously this key stored raw submission JSON.
        if isinstance(payload, dict):
            return payload

        raise ValueError(f"Invalid submission payload for {agent_id}")
