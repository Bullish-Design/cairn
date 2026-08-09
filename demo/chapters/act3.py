"""Act III — embedding APIs (guide §4.4, all verified sub-second, no sandbox):

- ch15: ``open_workspace`` + ``WorkspaceInspector`` + ``WorkspaceStats``
- ch16: ``AgentStateManager`` — typed state, namespacing, atomic increments
- ch17: ``WorkspaceCapability`` + ``ScriptedDriver`` + ``ProjectView``
- ch18: ``TaskQueue`` + ``with_retry`` (retires ``scripts/demo_cairn_library.py``)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cairn.runtime.driver import DriverStep, ProjectView, ScriptedDriver, WorkspaceCapability
from cairn.runtime.inspection import WorkspaceInspector
from cairn.runtime.state import AgentStateManager
from cairn.runtime.workspace_manager import open_workspace
from cairn.orchestrator.queue import TaskPriority, TaskQueue
from cairn.utils.retry import RetryStrategy
from pydantic import BaseModel

if TYPE_CHECKING:
    from demo.narrator import Narrator
    from demo.runner_ctx import ChapterContext

ACT_NUMERAL = "III"
ACT_TITLE = "Embedding APIs"


class _TurnState(BaseModel):
    turn: int
    context: dict[str, Any]


@dataclass
class ActIIIState:
    scratch: Path = field(default=None)


async def run(narrator: "Narrator", ctx: "ChapterContext", only: str | None = None) -> None:
    narrator.act(ACT_NUMERAL, ACT_TITLE)
    narrator.say(
        """
        Cairn is also a library.  These chapters embed it directly — no
        daemon, no sandbox, all sub-second — and double as the maintained
        replacement for the retired ``scripts/demo_cairn_library.py``.
        """
    )
    state = ActIIIState(scratch=ctx.out_dir / "act3-workspaces")
    state.scratch.mkdir(parents=True, exist_ok=True)
    try:
        for cid, title, fn in CHAPTERS:
            if only and cid != only:
                continue
            narrator.chapter(cid, title)
            await fn(narrator, ctx, state)
    finally:
        for db in state.scratch.glob("*.db"):
            db.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# ch15 — open_workspace + WorkspaceInspector + WorkspaceStats
# ---------------------------------------------------------------------------

async def _ch15_workspace_inspector(narrator: "Narrator", ctx: "ChapterContext", state: ActIIIState) -> None:
    narrator.say(
        """
        ``open_workspace`` opens an fsdantic workspace with Cairn's
        concurrency defaults (WAL, busy timeout); ``WorkspaceInspector``
        reads one without touching it.  We create a small workspace, write a
        couple of files, and inspect it read-only:
        """
    )
    narrator.code(
        """
        ws = await open_workspace("demo/out/act3-workspaces/inspect.db")
        await ws.files.write("src/alpha.py", "x = 1")
        await ws.files.write("README.md", "# inspect me")

        async with await WorkspaceInspector.from_path("demo/out/act3-workspaces/inspect.db") as inspector:
            stats = await inspector.stats()
            tree = await inspector.tree("/")
        await ws.close()
        """
    )
    db = state.scratch / "inspect.db"
    ws = await open_workspace(db)
    try:
        await ws.files.write("src/alpha.py", "x = 1")
        await ws.files.write("README.md", "# inspect me")
        await ws.close()

        async with await WorkspaceInspector.from_path(db) as inspector:
            stats = await inspector.stats()
            listing = await inspector.list_dir("/")
            readme = await inspector.read("README.md")

        narrator.capture(
            "WorkspaceInspector",
            "\n".join(
                [
                    f"stats   = WorkspaceStats(file_count={stats.file_count}, dir_count={stats.dir_count}, total_bytes={stats.total_bytes})",
                    f"listing = {listing}",
                    f"read    = {readme!r}",
                ]
            ),
        )
        narrator.prove("the inspector counts the files", stats.file_count == 2)
        narrator.prove("the inspector lists the root", "src" in listing)
    finally:
        await _safe_close(ws)


# ---------------------------------------------------------------------------
# ch16 — AgentStateManager
# ---------------------------------------------------------------------------

async def _ch16_agent_state(narrator: "Narrator", ctx: "ChapterContext", state: ActIIIState) -> None:
    narrator.say(
        """
        ``AgentStateManager`` gives one agent a namespaced key-value store
        with typed round-trips and atomic counters.  Namespacing means two
        agents can use the same key without colliding; the atomic
        ``increment`` means 50 concurrent increments land exactly 50 — no
        lost updates:
        """
    )
    narrator.code(
        """
        state = AgentStateManager(ws, "agent-1")
        await state.set("last_file", "/src/main.py")
        await state.set_typed("turn_state", TurnState(turn=1, context={}))
        await state.increment("counter")
        """
    )
    db = state.scratch / "state.db"
    ws = await open_workspace(db)
    try:
        agent_a = AgentStateManager(ws, "agent-a")
        agent_b = AgentStateManager(ws, "agent-b")

        await agent_a.set("last_file", "/src/main.py")
        await agent_a.set_typed("turn_state", _TurnState(turn=1, context={"phase": "review"}))
        await agent_b.set("last_file", "/src/other.py")  # same key, different agent

        turn = await agent_a.increment_turn()
        typed = await agent_a.get_typed("turn_state", _TurnState)

        await asyncio.gather(*(agent_a.increment("counter") for _ in range(50)))
        counter = await agent_a.get("counter")

        narrator.capture(
            "AgentStateManager",
            "\n".join(
                [
                    f"agent_a last_file = {await agent_a.get('last_file')!r}",
                    f"agent_b last_file = {await agent_b.get('last_file')!r}   (namespacing holds)",
                    f"increment_turn    = {turn}",
                    f"typed round-trip  = {typed.model_dump()}",
                    f"50 concurrent increments -> {counter}",
                ]
            ),
        )
        narrator.prove("namespacing keeps the agents' keys apart", (await agent_a.get("last_file")) != (await agent_b.get("last_file")))
        narrator.prove("typed models round-trip", typed is not None and typed.turn == 1 and typed.context == {"phase": "review"})
        narrator.prove("50 concurrent increments land exactly 50", counter == 50)
    finally:
        await _safe_close(ws)


# ---------------------------------------------------------------------------
# ch17 — WorkspaceCapability + ScriptedDriver + ProjectView
# ---------------------------------------------------------------------------

async def _ch17_driver(narrator: "Narrator", ctx: "ChapterContext", state: ActIIIState) -> None:
    narrator.say(
        """
        ``WorkspaceCapability`` is the narrow, path-validated capability a
        driver's model client receives — read/list/search/write/delete inside
        one bounded root, never host paths.  ``ScriptedDriver`` executes an
        explicit step plan under a hard ``step_limit``:
        """
    )
    narrator.code(
        """
        cap = WorkspaceCapability(root)
        driver = ScriptedDriver([
            DriverStep("write", "src/app.py", "print('hi')\\n"),
            DriverStep("read", "src/app.py"),
            DriverStep("submit", "wrote the app", "src/app.py"),
        ])
        submission = await driver.run("build the app", cap, step_limit=3)
        """
    )
    root = state.scratch / "cap-root"
    root.mkdir(parents=True, exist_ok=True)
    cap = WorkspaceCapability(root)
    steps = [
        DriverStep(action="write", path="src/app.py", content="print('hi')\n"),
        DriverStep(action="read", path="src/app.py"),
        DriverStep(action="submit", content="wrote the app", path="src/app.py"),
    ]
    driver = ScriptedDriver(steps)
    submission = await driver.run("build the app", cap, step_limit=3)

    # A step limit of 2 would stop before the submit.
    short_driver = ScriptedDriver(steps)
    truncated = await short_driver.run("build the app", cap, step_limit=2)

    narrator.capture(
        "ScriptedDriver",
        "\n".join(
            [
                f"submission (step_limit=3) = {submission}",
                f"truncated (step_limit=2)  = {truncated}",
                f"src/app.py content        = {await cap.read('src/app.py')!r}",
            ]
        ),
    )
    narrator.prove("the driver's writes land in the bounded root", await cap.read("src/app.py") == "print('hi')\n")
    narrator.prove("the step limit is honored (submit never ran)", truncated["summary"] == "build the app" and submission["summary"] == "wrote the app")
    narrator.prove("the capability refuses traversal", not await _cap_accepts_escape(cap))

    narrator.say(
        """
        ``ProjectView`` is the read-only snapshot providers receive (chapter
        02): gitignore-aware and no-follow, with **no write surface at all** —
        a provider physically cannot mutate the canonical tree:
        """
    )
    view = ProjectView(ctx.project_root)
    view_listing = await view.list_dir(".")
    stat = await view.stat("run.sh")
    narrator.capture(
        "ProjectView over the fixture",
        "\n".join(
            [
                f"hasattr(view, 'write') = {hasattr(view, 'write')}",
                f"list_dir('.')          = {view_listing}",
                f"stat('run.sh')         = {stat}",
            ]
        ),
    )
    narrator.prove("ProjectView exposes no write surface", not hasattr(view, "write"))
    narrator.prove("ProjectView is gitignore-aware (no secrets.env)", "secrets.env" not in view_listing)
    narrator.prove("ProjectView reports file metadata", stat.get("kind") == "file" and (stat.get("size") or 0) > 0)


async def _cap_accepts_escape(cap: WorkspaceCapability) -> bool:
    """Does the capability reject a traversal write?  (It must.)"""
    from cairn.core.exceptions import SecurityError

    try:
        await cap.write("../escape.txt", "nope")
        return True
    except SecurityError:
        return False


# ---------------------------------------------------------------------------
# ch18 — TaskQueue + with_retry
# ---------------------------------------------------------------------------

async def _ch18_queue_retry(narrator: "Narrator", ctx: "ChapterContext", state: ActIIIState) -> None:
    narrator.say(
        """
        ``TaskQueue`` is the bounded priority queue the orchestrator runs on
        (chapter 14 dequeue order, shown here via the public API), and
        ``with_retry`` / ``RetryStrategy`` absorb transient failures with
        exponential backoff:
        """
    )
    narrator.code(
        """
        queue = TaskQueue(max_size=10)
        await queue.enqueue("b", TaskPriority.NORMAL)
        await queue.enqueue("a", TaskPriority.URGENT)
        first = await queue.dequeue_wait()   # -> a (URGENT beats NORMAL)

        retry = RetryStrategy(max_attempts=3, initial_delay=0.01)
        result = await retry.with_retry(flaky_operation, retry_exceptions=(RuntimeError,))
        """
    )
    queue = TaskQueue(max_size=10)
    await queue.enqueue("normal task", TaskPriority.NORMAL)
    await queue.enqueue("urgent task", TaskPriority.URGENT)
    await queue.enqueue("low task", TaskPriority.LOW)
    first = await queue.dequeue_wait()
    second = await queue.dequeue_wait()
    third = await queue.dequeue_wait()

    attempts = 0

    async def flaky_operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient failure")
        return "ok"

    retry = RetryStrategy(max_attempts=3, initial_delay=0.01, max_delay=0.02)
    result = await retry.with_retry(flaky_operation, retry_exceptions=(RuntimeError,))

    narrator.capture(
        "TaskQueue + RetryStrategy",
        "\n".join(
            [
                f"dequeue order = [{first.task}, {second.task}, {third.task}]",
                f"retry result  = {result!r} after {attempts} attempts",
            ]
        ),
    )
    narrator.prove("priority ordering holds", [first.task, second.task, third.task] == ["urgent task", "normal task", "low task"])
    narrator.prove("retry succeeded on attempt 3", result == "ok" and attempts == 3)


async def _safe_close(ws: Any) -> None:
    try:
        await ws.close()
    except Exception:  # noqa: BLE001, S110 - best-effort cleanup
        pass


CHAPTERS = [
    ("15", "open_workspace + WorkspaceInspector + WorkspaceStats", _ch15_workspace_inspector),
    ("16", "AgentStateManager — typed state, namespacing, atomic increment", _ch16_agent_state),
    ("17", "WorkspaceCapability + ScriptedDriver + ProjectView", _ch17_driver),
    ("18", "TaskQueue + with_retry", _ch18_queue_retry),
]
