from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import AgentFSOptions, Fsdantic

from cairn.agent_tools import CairnAgentTools
from cairn.lifecycle import SUBMISSION_KEY, SubmissionRecord


@pytest.mark.asyncio
async def test_tool_contract_read_write_search_and_submit(tmp_path: Path) -> None:
    stable = await Fsdantic.open_with_options(AgentFSOptions(path=str(tmp_path / "stable.db")))
    agent = await Fsdantic.open_with_options(AgentFSOptions(path=str(tmp_path / "agent.db")))

    try:
        await stable.files.write("docs/base.txt", "hello from stable")

        tools = CairnAgentTools(agent_id="agent-1", agent_fs=agent, stable_fs=stable)

        assert await tools.read_file("docs/base.txt") == "hello from stable"

        assert await tools.write_file("notes/todo.txt", "todo: ship it") is True
        assert await tools.read_file("notes/todo.txt") == "todo: ship it"

        matches = await tools.search_content("ship", path="notes")
        assert len(matches) == 1
        assert matches[0]["file"] == "notes/todo.txt"
        assert matches[0]["line"] == 1
        assert "ship it" in matches[0]["text"]

        assert await tools.submit_result("done", ["notes/todo.txt"]) is True
        repo = agent.kv.repository(prefix="", model_type=SubmissionRecord)
        saved = await repo.load(SUBMISSION_KEY)
        assert saved is not None
        assert saved.agent_id == "agent-1"
        assert saved.submission["summary"] == "done"
        assert saved.submission["changed_files"] == ["notes/todo.txt"]
    finally:
        await agent.close()
        await stable.close()
