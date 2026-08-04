"""Tests for AgentStateManager KV state (namespacing + atomic increments)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.runtime.state import AgentStateManager


async def _make_state(tmp_path: Path, agent_id: str = "agent-state") -> tuple[AgentStateManager, object]:
    workspace = await Fsdantic.open(path=str(tmp_path / "state.db"))
    return AgentStateManager(workspace, agent_id), workspace


@pytest.mark.asyncio
async def test_increment_creates_at_zero_and_increments(tmp_path: Path) -> None:
    state, workspace = await _make_state(tmp_path)
    try:
        assert await state.increment("counter") == 1
        assert await state.increment("counter") == 2
        assert await state.increment("counter", amount=5) == 7
        assert await state.get("counter") == 7
    finally:
        await workspace.close()


@pytest.mark.asyncio
async def test_increment_turn_starts_at_one(tmp_path: Path) -> None:
    state, workspace = await _make_state(tmp_path)
    try:
        assert await state.get_turn() == 0
        assert await state.increment_turn() == 1
        assert await state.increment_turn() == 2
        assert await state.get_turn() == 2
    finally:
        await workspace.close()


@pytest.mark.asyncio
async def test_increment_is_namespaced_per_agent(tmp_path: Path) -> None:
    workspace = await Fsdantic.open(path=str(tmp_path / "state-ns.db"))
    try:
        a = AgentStateManager(workspace, "agent-a")
        b = AgentStateManager(workspace, "agent-b")
        await a.increment("turn")
        await a.increment("turn")
        await b.increment("turn")
        assert await a.get("turn") == 2
        assert await b.get("turn") == 1
        assert await workspace.kv.get("agent:agent-a:turn") == 2
    finally:
        await workspace.close()


@pytest.mark.asyncio
async def test_increment_non_numeric_value_resets_to_zero(tmp_path: Path) -> None:
    state, workspace = await _make_state(tmp_path)
    try:
        await state.set("counter", "not-a-number")
        # Legacy behavior preserved: a corrupted value resets to 0, then the
        # increment applies atomically.
        assert await state.increment("counter") == 1
    finally:
        await workspace.close()


@pytest.mark.asyncio
async def test_concurrent_increments_no_lost_updates(tmp_path: Path) -> None:
    """The atomic KV increment (fsdantic >= 0.5.0) serializes per key, so 20
    concurrent increments must all land — no lost updates."""
    state, workspace = await _make_state(tmp_path, agent_id="agent-race")
    try:
        await asyncio.gather(*(state.increment("counter") for _ in range(20)))
        assert await state.get("counter") == 20
    finally:
        await workspace.close()
