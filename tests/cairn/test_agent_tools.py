from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.runtime.external_functions import create_external_functions
from cairn.orchestrator.lifecycle import SUBMISSION_KEY, SubmissionRecord


@pytest.mark.asyncio
async def test_tool_contract_read_write_search_and_submit(tmp_path: Path) -> None:
    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))

    try:
        await stable.files.write("docs/base.txt", "hello from stable")

        tools = create_external_functions(agent_id="agent-1", agent_fs=agent, stable_fs=stable)

        assert await tools["read_file"]("docs/base.txt") == "hello from stable"

        assert await tools["write_file"]("notes/todo.txt", "todo: ship it") is True
        assert await tools["read_file"]("notes/todo.txt") == "todo: ship it"

        matches = await tools["search_content"]("ship", path="notes")
        assert len(matches) == 1
        assert matches[0]["file"] == "notes/todo.txt"
        assert matches[0]["line"] == 1
        assert "ship it" in matches[0]["text"]

        assert await tools["submit_result"]("done", ["notes/todo.txt"]) is True
        repo = agent.kv.repository(prefix="", model_type=SubmissionRecord)
        saved = await repo.load(SUBMISSION_KEY)
        assert saved is not None
        assert saved.agent_id == "agent-1"
        assert saved.submission["summary"] == "done"
        assert saved.submission["changed_files"] == ["notes/todo.txt"]
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.asyncio
async def test_search_content_defaults_to_global_scope(tmp_path: Path) -> None:
    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))

    try:
        await stable.files.write("notes/todo.txt", "find me")
        await stable.files.write("src/module.py", "find me")

        tools = create_external_functions(agent_id="agent-1", agent_fs=agent, stable_fs=stable)
        matches = await tools["search_content"]("find me", path=".")

        assert {match["file"] for match in matches} == {"notes/todo.txt", "src/module.py"}
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.asyncio
async def test_search_content_respects_scoped_patterns(tmp_path: Path) -> None:
    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))

    try:
        await stable.files.write("src/target.py", "needle")
        await stable.files.write("src/nested/inner.py", "needle")
        await stable.files.write("docs/readme.md", "needle")

        tools = create_external_functions(agent_id="agent-1", agent_fs=agent, stable_fs=stable)

        scoped = await tools["search_content"]("needle", path="src/**")
        assert {match["file"] for match in scoped} == {"src/target.py", "src/nested/inner.py"}

        dir_scoped = await tools["search_content"]("needle", path="src")
        assert {match["file"] for match in dir_scoped} == {"src/target.py", "src/nested/inner.py"}
    finally:
        await agent.close()
        await stable.close()


@pytest.mark.asyncio
async def test_search_content_invalid_path_handling_is_consistent(tmp_path: Path) -> None:
    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    agent = await Fsdantic.open(path=str(tmp_path / "agent.db"))

    try:
        tools = create_external_functions(agent_id="agent-1", agent_fs=agent, stable_fs=stable)

        with pytest.raises(ValueError, match="Invalid path"):
            await tools["search_content"]("needle", path="../outside")

        with pytest.raises(ValueError, match="Invalid path"):
            await tools["search_content"]("needle", path="/absolute")
    finally:
        await agent.close()
        await stable.close()
