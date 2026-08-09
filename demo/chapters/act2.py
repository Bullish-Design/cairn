"""Act II — where Cairn earns its keep (guide §4.4): the agent lies, fail-closed
accept, the boundary, limits, changeset-steering, concurrency.

The act's orchestrator runs with ``max_concurrent_agents=2`` so ch14's
wall-clock concurrency claim is measured against the *same* orchestrator the
other chapters use.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cairn.core.exceptions import WorkspaceMergeError
from cairn.orchestrator.orchestrator import TERMINAL_STATES
from cairn.orchestrator.queue import TaskPriority, TaskQueue
from cairn.providers.providers import InlineCodeProvider
from cairn.runtime.agent import AgentState
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings

if TYPE_CHECKING:
    from demo.narrator import Narrator
    from demo.runner_ctx import ChapterContext

ACT_NUMERAL = "II"
ACT_TITLE = "Where Cairn earns its keep"


@dataclass
class ActIIState:
    orch: Any = field(default=None)


async def run(narrator: "Narrator", ctx: "ChapterContext", only: str | None = None) -> None:
    narrator.act(ACT_NUMERAL, ACT_TITLE)
    state = ActIIState()
    state.orch = ctx.make_orchestrator(config=OrchestratorSettings(start_worker_on_init=True, max_concurrent_agents=2))
    await state.orch.initialize()
    try:
        for cid, title, fn in CHAPTERS:
            if only and cid != only:
                continue
            narrator.chapter(cid, title)
            await fn(narrator, ctx, state)
    finally:
        await state.orch.shutdown()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _run_agent(state: ActIIState, task: str, **kwargs: Any) -> tuple[str, Any]:
    orch = state.orch
    agent_id = await orch.spawn_agent(task, TaskPriority.HIGH)
    record = await orch.wait_for_agent(agent_id, timeout=120.0)
    return agent_id, record


def _run_log(ctx: "ChapterContext", agent_id: str) -> str:
    """The sandbox run log is a plain file in the workspace — no DB access."""
    path = ctx.home / "workspaces" / agent_id / ".cairn" / "run.log"
    return path.read_text(encoding="utf-8") if path.exists() else "(no run.log)"


def _mirror_entry(ctx: "ChapterContext", agent_id: str) -> dict[str, Any]:
    """The lifecycle mirror is the CLI's public read path."""
    mirror = json.loads((ctx.home / "state" / "lifecycle.json").read_text(encoding="utf-8"))
    return mirror.get(agent_id, {})


def _short_error(error: str | None) -> str:
    """Trim an error message at the context suffix for readable captures."""
    if not error:
        return "(none)"
    return error.split("(agent_id=")[0].strip() or error


# ---------------------------------------------------------------------------
# ch09 — The agent lies
# ---------------------------------------------------------------------------

async def _ch09_agent_lies(narrator: "Narrator", ctx: "ChapterContext", state: ActIIState) -> None:
    narrator.say(
        """
        Agents are fallible, and sometimes they lie.  The submission payload
        (``summary`` + ``changed_files``) is the agent's *self-report*; the
        changeset the executor computed from the workspace diff is the truth.
        Here is an agent that wrote a file and claimed it did something else
        entirely:
        """
    )
    narrator.code(
        """
        write_file("src/sneaky.py", 'print("I should not be here")\\n')
        submit_result("fixed a typo in the README", ["README.md"])
        """
    )
    state.orch.code_provider = InlineCodeProvider()
    task = (
        'write_file("src/sneaky.py", \'print("I should not be here")\\n\')\n'
        'submit_result("fixed a typo in the README", ["README.md"])'
    )
    agent_id, record = await _run_agent(state, task)
    entry = _mirror_entry(ctx, agent_id)

    narrator.capture(
        f"agent {agent_id}",
        "\n".join(
            [
                f"written        = {entry.get('run_written') or []}",
                f"claim_mismatch = {record.claim_mismatch}",
                f"submission     = {json.dumps(record.submission, sort_keys=True)}",
            ]
        ),
    )
    narrator.prove("the computed changeset recorded src/sneaky.py", entry.get("run_written") == ["src/sneaky.py"])
    narrator.prove("the self-report is flagged as a mismatch", record.claim_mismatch is True)


# ---------------------------------------------------------------------------
# ch10 — Fail-closed accept
# ---------------------------------------------------------------------------

async def _ch10_fail_closed(narrator: "Narrator", ctx: "ChapterContext", state: ActIIState) -> None:
    narrator.say(
        """
        Accept revalidates the base every touched path had at run start.  If
        a human changed the tree while the agent was working, accept refuses
        with ``ACCEPT_STALE_BASE`` — it never silently overwrites a newer
        edit.  The agent stays usable, and ``--force`` explicitly lets the
        agent's version win:
        """
    )
    state.orch.code_provider = InlineCodeProvider()
    task = (
        'write_file("src/main.py", \'print("agent version of main")\\n\')\n'
        'write_file("src/util.py", \'print("agent version of util")\\n\')\n'
        'submit_result("rewrote both modules", ["src/main.py", "src/util.py"])'
    )
    agent_id, _record = await _run_agent(state, task)

    # A human edits the tree while the agent is in review.
    (ctx.project_root / "src" / "main.py").write_text('print("human edit after the agent ran")\n', encoding="utf-8")

    try:
        await state.orch.accept_agent(agent_id)
        refused = "(accept unexpectedly succeeded)"
    except WorkspaceMergeError as exc:
        refused = f"{exc.error_code}  stale_paths={exc.context.get('stale_paths')}"
    narrator.capture("accept without --force", refused)
    narrator.prove("accept refused with ACCEPT_STALE_BASE", refused.startswith("ACCEPT_STALE_BASE"))
    narrator.prove("the human edit is untouched", "human edit after the agent ran" in (ctx.project_root / "src" / "main.py").read_text())

    # The agent is still reviewable — the refusal did not consume it.
    record = await state.orch.lifecycle.load(agent_id)
    narrator.capture("agent after the refusal", f"state = {record.state.value}")
    narrator.prove("the agent stays usable (still REVIEWING)", record.state is AgentState.REVIEWING)

    stats = await state.orch.accept_agent(agent_id, force=True)
    narrator.capture("accept with --force", json.dumps(stats, sort_keys=True))
    narrator.prove("--force accepts and the agent's version wins", "agent version of main" in (ctx.project_root / "src" / "main.py").read_text())


# ---------------------------------------------------------------------------
# ch11 — The boundary, probed
# ---------------------------------------------------------------------------

BOUNDARY_TASK = r'''
import json
import os
import socket
import sys

probe = {}
probe["environ_entries"] = len(os.environ)
probe["environ"] = dict(os.environ)
probe["stdin_isatty"] = sys.stdin.isatty()
try:
    os.listdir(os.path.expanduser("~/.ssh"))
    probe["home_ssh_readable"] = True
except OSError:
    probe["home_ssh_readable"] = False
try:
    socket.create_connection(("1.1.1.1", 53), timeout=2)
    probe["network"] = "connected"
except OSError:
    probe["network"] = "blocked (OSError)"
try:
    with open("/etc/passwd", "w") as fh:
        fh.write("pwned")
    probe["write_etc_passwd"] = "WROTE"
except OSError as exc:
    probe["write_etc_passwd"] = f"blocked ({type(exc).__name__})"
try:
    with open("../escape.txt", "w") as fh:
        fh.write("raw open escapes the helpers")
    probe["raw_escape_write"] = "WROTE"
except OSError as exc:
    probe["raw_escape_write"] = f"blocked ({type(exc).__name__})"
try:
    write_file("../escape.txt", "helper is the boundary")
    probe["helper_escape_write"] = "WROTE"
except Exception as exc:
    probe["helper_escape_write"] = f"{type(exc).__name__}: {exc}"
probe["uid"] = os.getuid()
write_file("probe.json", json.dumps(probe, sort_keys=True))
print(json.dumps(probe, indent=2, sort_keys=True))
submit_result("probed the boundary", [])
'''.strip()


async def _ch11_boundary(narrator: "Narrator", ctx: "ChapterContext", state: ActIIState) -> None:
    narrator.say(
        """
        What can task code actually reach?  The answer is measured from
        inside the sandbox, not assumed.  ``bwrap`` runs the task as an
        unprivileged user with only the materialized workspace writable.
        Note two framings that matter.  First: ``--clearenv`` is passed, but
        bubblewrap repopulates a handful of variables — the environment is
        *small*, not empty.  Second: the raw ``open("../escape.txt")`` below
        *succeeds* — the sandbox helpers are ergonomics, not the boundary.
        The write lands in an ephemeral mount outside the workspace, so it
        appears in no changeset and evaporates with the sandbox.  Confinement
        comes from what is mounted, not from what is validated:
        """
    )
    narrator.code(BOUNDARY_TASK)
    state.orch.code_provider = InlineCodeProvider()
    agent_id, _record = await _run_agent(state, BOUNDARY_TASK)

    log = _run_log(ctx, agent_id)
    narrator.capture(f"sandbox probe output (agent {agent_id})", log)

    probe_path = ctx.home / "workspaces" / agent_id / "probe.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    narrator.say(
        """
        Reading the probe's answers: the environment holds a handful of
        entries bubblewrap needs (not zero, and certainly not the host's
        env), stdin is not a tty, the host's ``~/.ssh`` is not reachable,
        the network is blocked, and ``/etc/passwd`` is not writable.
        """
    )
    narrator.prove("the environment is tiny (no host env leaked)", probe["environ_entries"] <= 30)
    sandbox_only = all(
        key.startswith("CAIRN_") or key in {"PATH", "PWD", "LC_CTYPE", "PYTHONUNBUFFERED"} for key in probe["environ"]
    )
    narrator.prove("no host environment leaked (only cairn-injected + sandbox internals)", sandbox_only)
    narrator.prove("stdin is not a tty", probe["stdin_isatty"] is False)
    narrator.prove("the host home is unreachable", probe["home_ssh_readable"] is False)
    narrator.prove("the network is blocked", probe["network"] == "blocked (OSError)")
    narrator.prove("/etc/passwd is not writable", probe["write_etc_passwd"].startswith("blocked"))
    narrator.prove("the sandbox runs as uid 65534 (nobody)", probe["uid"] == 65534)
    narrator.prove("the write_file helper rejects traversal", "traversal" in probe["helper_escape_write"] or "not allowed" in probe["helper_escape_write"])

    narrator.say(
        """
        The contrast is the point: the *helper* refused ``../escape.txt``,
        while raw ``open`` wrote it — and that write is unreachable.  It is
        not in the agent's changeset, and nothing on the host has it:
        """
    )
    escape_on_host = narrator.shell(["find", str(ctx.out_dir), "-name", "escape.txt"])
    narrator.prove("the raw escape write left nothing on the host", "escape.txt" not in escape_on_host)


# ---------------------------------------------------------------------------
# ch12 — Limits
# ---------------------------------------------------------------------------

async def _ch12_limits(narrator: "Narrator", ctx: "ChapterContext", state: ActIIState) -> None:
    narrator.say(
        """
        Limits are enforced by the host and legible afterwards.  A task that
        loops forever is killed at ``max_execution_time``; a task that asks
        for 400 MB against a 100 MB ``max_memory_bytes`` cap dies with
        ``MemoryError`` — and the partial log survives via the mirror, so
        ``cairn logs`` works on errored agents:
        """
    )
    state.orch.code_provider = InlineCodeProvider()
    original = state.orch.executor_settings

    # Timeout: an infinite loop under a 3s cap (prints periodically so the
    # log cap cannot beat the wall-clock timeout to the kill).
    state.orch.executor_settings = ExecutorSettings(max_execution_time=3.0, max_memory_bytes=128 * 1024 * 1024)
    loop_task = 'import time\nwhile True:\n    print("looping forever", flush=True)\n    time.sleep(0.5)'
    agent_loop, record_loop = await _run_agent(state, loop_task)
    narrator.capture(
        f"timeout agent {agent_loop}",
        f"state = {record_loop.state.value}\nerror = {_short_error(record_loop.error)}",
    )
    narrator.capture(f"run.log tail ({agent_loop})", _run_log(ctx, agent_loop)[-400:])
    narrator.prove("the infinite loop was killed with EXECUTION_TIMEOUT", (record_loop.error or "").startswith("[EXECUTION_TIMEOUT]"))
    narrator.prove("the kill reason is in the log", "killed after 3.0s" in _run_log(ctx, agent_loop))

    # Memory: 400MB request against a 100MB cap.
    state.orch.executor_settings = ExecutorSettings(max_execution_time=30.0, max_memory_bytes=100 * 1024 * 1024)
    mem_task = 'blob = bytearray(400 * 1024 * 1024)'
    agent_mem, record_mem = await _run_agent(state, mem_task)
    narrator.capture(
        f"memory agent {agent_mem}",
        f"state = {record_mem.state.value}\nerror = {_short_error(record_mem.error)}",
    )
    narrator.capture(f"run.log ({agent_mem})", _run_log(ctx, agent_mem))
    narrator.prove("the 400MB request failed against the 100MB cap", "MemoryError" in (record_mem.error or ""))

    state.orch.executor_settings = original

    # cairn logs via the CLI (the mirror carries the partial log).
    logs_out = narrator.shell(
        [
            *ctx.cli_cmd("logs", agent_loop, "--cairn-home", str(ctx.home), "--project-root", str(ctx.project_root)),
        ]
    )
    narrator.prove("cairn logs shows the errored agent's partial log", "looping forever" in logs_out)


# ---------------------------------------------------------------------------
# ch13 — A task cannot steer its changeset
# ---------------------------------------------------------------------------

async def _ch13_no_steering(narrator: "Narrator", ctx: "ChapterContext", state: ActIIState) -> None:
    narrator.say(
        """
        Admission rules are host state.  A task that rewrites ``.gitignore``
        to ``*.py`` cannot retroactively hide the ``.py`` file it just wrote
        — the changeset is computed against a filter built from the *project*
        tree, never from what the task writes.  The diff does not care about
        the task's self-reported intent:
        """
    )
    narrator.code(
        """
        write_file(".gitignore", "*.py")
        write_file("src/still_counted.py", "print('I still count')\\n")
        submit_result("cleaned up", ["src/still_counted.py", ".gitignore"])
        """
    )
    state.orch.code_provider = InlineCodeProvider()
    task = (
        'write_file(".gitignore", "*.py")\n'
        'write_file("src/still_counted.py", \'print("I still count")\\n\')\n'
        'submit_result("cleaned up", ["src/still_counted.py", ".gitignore"])'
    )
    agent_id, _record = await _run_agent(state, task)

    # Ground truth: the run record the executor wrote (what accept would apply).
    orch_ctx = state.orch.active_agents[agent_id]
    agent_fs = await state.orch._get_agent_workspace(orch_ctx)
    run = await state.orch._load_run_record(agent_fs)
    written = sorted(run.written) if run else []

    workdir = ctx.home / "workspaces" / agent_id
    narrator.capture(
        f"agent {agent_id}",
        "\n".join(
            [
                f"workspace .gitignore : {workdir.joinpath('.gitignore').read_text()!r}",
                f"workspace src/       : {sorted(p.name for p in (workdir / 'src').iterdir())}",
                f"executor changeset   : written={written}",
            ]
        ),
    )
    narrator.say(
        """
        The naive diff a tool might compute from the *workspace's* own
        ``.gitignore`` would lie here — with ``*.py`` in effect, the `.py`
        files would vanish from its view of the workspace.  That is exactly
        why the executor's admission filter is host state, bound once per run
        to the project tree and never rebuilt from anything the task wrote.
        """
    )
    narrator.prove("the task-written .gitignore is part of the changeset", ".gitignore" in written)
    narrator.prove("the new rule does not hide the .py file", "src/still_counted.py" in written)


# ---------------------------------------------------------------------------
# ch14 — Concurrency
# ---------------------------------------------------------------------------

async def _ch14_concurrency(narrator: "Narrator", ctx: "ChapterContext", state: ActIIState) -> None:
    narrator.say(
        """
        Two things about concurrency are worth measuring, and one is worth
        narrating honestly.  First, the *dequeue* order of the priority
        ``TaskQueue`` is deterministic — URGENT before HIGH before NORMAL
        before LOW, with FIFO tie-breaking:
        """
    )
    queue = TaskQueue()
    for task, priority in [
        ("normal", TaskPriority.NORMAL),
        ("urgent", TaskPriority.URGENT),
        ("high", TaskPriority.HIGH),
        ("low", TaskPriority.LOW),
    ]:
        await queue.enqueue(task, priority)
    dequeued = [task.task for task in [await queue.dequeue_wait(), await queue.dequeue_wait(), await queue.dequeue_wait(), await queue.dequeue_wait()]]
    narrator.capture("TaskQueue dequeue order", str(dequeued))
    narrator.prove("dequeue order follows priority", dequeued == ["urgent", "high", "normal", "low"])

    narrator.say(
        """
        Second, concurrency is *limited*: with ``max_concurrent_agents=2``,
        five one-second tasks take roughly ``ceil(5/2) = 3`` seconds, not
        one.  The claim is asserted by wall clock, not by completion order —
        completion order is *not* deterministic, because a queue is not a
        preemptive scheduler: tasks already running are not interrupted, so
        even an URGENT task submitted late waits for a free slot.  That
        subtlety is the honest version of the story:
        """
    )
    state.orch.code_provider = InlineCodeProvider()
    one_second = 'import time\ntime.sleep(1.0)\nwrite_file("done.txt", "done")\nsubmit_result("done", ["done.txt"])'
    started = time.monotonic()
    agents = [await state.orch.spawn_agent(one_second, TaskPriority.NORMAL) for _ in range(5)]
    for agent_id in agents:
        await state.orch.wait_for_agent(agent_id, timeout=120.0)
    elapsed = time.monotonic() - started

    narrator.capture(
        "five 1s tasks at max_concurrent_agents=2",
        f"elapsed = {elapsed:.2f}s   (ceil(5/2) * 1s = 3.0s expected)",
    )
    narrator.prove("the wall clock reflects the concurrency cap", 2.5 < elapsed < 4.5)


CHAPTERS = [
    ("09", "The agent lies", _ch09_agent_lies),
    ("10", "Fail-closed accept", _ch10_fail_closed),
    ("11", "The boundary, probed", _ch11_boundary),
    ("12", "Limits", _ch12_limits),
    ("13", "A task cannot steer its changeset", _ch13_no_steering),
    ("14", "Concurrency", _ch14_concurrency),
]
