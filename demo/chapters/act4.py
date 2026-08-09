"""Act IV — daemon & thin client (guide §4.3, §4.4).

ch19 starts and stops its own daemon against the fixture and drives it with
the real CLI, every step as a subprocess.  ch20 (recovery after a daemon
death) is gated behind ``--include-recovery`` and skips loudly otherwise.

Flags are placed *after* the subcommand throughout: that position works in
both the fixed and unfixed CLI, so the transcript stays valid for readers on
an older checkout (guide §4.3).
"""

from __future__ import annotations

import asyncio
import gc
import json
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from demo.narrator import Narrator
    from demo.runner_ctx import ChapterContext

ACT_NUMERAL = "IV"
ACT_TITLE = "Daemon & CLI"

#: Task files the daemon runs, written into the fixture before ch19.
ACT4_TASK = (
    'write_file("src/added.py", "def added():\\n    return 1\\n")\n'
    'write_file("src/main.py", \'print("act4 version of main")\\n\')\n'
    'submit_result("added a module and updated main", ["src/added.py", "src/main.py"])'
)

RECOVERY_TASK = (
    "import time\n"
    "time.sleep(30)\n"
    'write_file("src/never_finishes.py", "print(1)\\n")\n'
    'submit_result("never lands", ["src/never_finishes.py"])'
)


@dataclass
class ActIVState:
    daemon: subprocess.Popen[str] | None = field(default=None)
    daemon_log: Path | None = None
    daemon_log_handle: Any = field(default=None)
    task_file: Path | None = None


async def run(narrator: Narrator, ctx: ChapterContext, only: str | None = None) -> None:
    narrator.act(ACT_NUMERAL, ACT_TITLE)
    narrator.say(
        """
        The daemon owns the databases and the control socket; the CLI is a
        thin client that never constructs an orchestrator.  This act starts
        a real daemon, drives it with real CLI subprocesses, and shuts it
        down — flags after the subcommand throughout (the position that
        works in both the fixed and unfixed CLI).
        """
    )
    state = ActIVState()
    state.daemon_log = ctx.out_dir / "act4-daemon.log"
    state.task_file = ctx.project_root / "tasks" / "act4_task.py"
    state.task_file.parent.mkdir(parents=True, exist_ok=True)
    state.task_file.write_text(ACT4_TASK, encoding="utf-8")
    recovery_file = ctx.project_root / "tasks" / "recovery_task.py"
    recovery_file.write_text(RECOVERY_TASK, encoding="utf-8")
    try:
        for cid, title, fn in CHAPTERS:
            if only and cid != only:
                continue
            narrator.chapter(cid, title)
            await fn(narrator, ctx, state)
    finally:
        await _stop_daemon(state, hard=True)


# ---------------------------------------------------------------------------
# Daemon lifecycle helpers
# ---------------------------------------------------------------------------


async def _start_daemon(narrator: Narrator, ctx: ChapterContext, state: ActIVState) -> None:
    """Launch ``cairn up`` in a subprocess and wait for the control socket."""
    # pyturso (native) keeps an exclusive lock on an opened database until the
    # handle is garbage collected — a closed connection is not enough.  The
    # earlier acts opened the project's bin.db in this process, so force a GC
    # cycle before the daemon subprocess (a *separate* process) needs it.
    gc.collect()
    cmd = [*ctx.cli_cmd("up", "--project-root", str(ctx.project_root), "--cairn-home", str(ctx.act4_home))]
    if state.daemon_log_handle is not None:
        state.daemon_log_handle.close()
    state.daemon_log_handle = state.daemon_log.open("w", encoding="utf-8") if state.daemon_log else None

    def _spawn() -> subprocess.Popen[str]:
        return subprocess.Popen(
            cmd,
            env=ctx.cli_env(),
            stdout=state.daemon_log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    state.daemon = await asyncio.to_thread(_spawn)
    socket_path = ctx.act4_home / "state" / "orchestrator.sock"
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if socket_path.exists() and state.daemon.poll() is None:
            return
        if state.daemon.poll() is not None:
            raise RuntimeError(f"daemon exited early: {_daemon_log_tail(state)}")
        await asyncio.sleep(0.1)
    raise RuntimeError(f"daemon socket never appeared: {_daemon_log_tail(state)}")


async def _stop_daemon(state: ActIVState, *, hard: bool) -> None:
    """Terminate the daemon; ``hard`` sends SIGKILL (a crash), else SIGTERM."""
    if state.daemon is None or state.daemon.poll() is not None:
        if state.daemon_log_handle is not None:
            state.daemon_log_handle.close()
            state.daemon_log_handle = None
        return
    try:
        state.daemon.send_signal(signal.SIGKILL if hard else signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        state.daemon.wait(timeout=10)
    except subprocess.TimeoutExpired:
        state.daemon.kill()
        state.daemon.wait(timeout=5)
    if state.daemon_log_handle is not None:
        state.daemon_log_handle.close()
        state.daemon_log_handle = None
    state.daemon = None


def _daemon_log_tail(state: ActIVState) -> str:
    if state.daemon_log and state.daemon_log.exists():
        return state.daemon_log.read_text(encoding="utf-8")[-800:]
    return "(no daemon log)"


async def _wait_for_state(
    ctx: ChapterContext,
    agent_id: str,
    *,
    wanted: set[str],
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Poll ``cairn status`` until the agent reaches a wanted state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:

        def _status() -> tuple[int, str]:
            proc = subprocess.run(
                [
                    *ctx.cli_cmd(
                        "status", agent_id, "--cairn-home", str(ctx.act4_home), "--project-root", str(ctx.project_root)
                    )
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            return proc.returncode, proc.stdout

        rc, stdout = await asyncio.to_thread(_status)
        if rc == 0:
            payload = json.loads(stdout)
            if payload.get("state") in wanted:
                return payload
        await asyncio.sleep(0.2)
    raise AssertionError(f"agent {agent_id} never reached {sorted(wanted)} within {timeout}s")


def _run_cli(ctx: ChapterContext, *argv: str) -> tuple[int, str]:
    """Run a CLI subprocess with the Act IV paths; return (rc, combined out)."""
    proc = subprocess.run(
        [*ctx.cli_cmd(*argv, "--cairn-home", str(ctx.act4_home), "--project-root", str(ctx.project_root))],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr
    return proc.returncode, output.rstrip()


# ---------------------------------------------------------------------------
# ch19 — Daemon + thin client
# ---------------------------------------------------------------------------


async def _ch19_daemon(narrator: Narrator, ctx: ChapterContext, state: ActIVState) -> None:
    narrator.say(
        """
        Step by step, through the real CLI.  First, mutating commands refuse
        cleanly when no daemon is running — exit 2, with guidance, no
        traceback:
        """
    )
    rc, out = _run_cli(ctx, "spawn", "tasks/act4_task.py")
    narrator.capture("cairn spawn (no daemon)", f"exit code: {rc}\n{out}")
    narrator.prove("spawn without a daemon exits 2 with guidance", rc == 2 and "No Cairn daemon is running" in out)

    narrator.say(
        """
        Now the daemon starts.  Ownership is the control socket under
        ``$CAIRN_HOME/state/``; the project's ``.agentfs/`` holds the
        metadata databases.  A second ``cairn up`` against the same home is
        refused instead of crashing:
        """
    )
    await _start_daemon(narrator, ctx, state)
    socket_path = ctx.act4_home / "state" / "orchestrator.sock"
    bin_db = ctx.project_root / ".agentfs" / "bin.db"
    narrator.capture(
        "daemon up",
        f"control socket present : {socket_path.exists()} ({socket_path})\nproject .agentfs/bin.db: {bin_db.exists()}",
    )
    rc2, out2 = _run_cli(ctx, "up")
    narrator.capture("cairn up (second time)", f"exit code: {rc2}\n{out2}")
    narrator.prove("the control socket is the ownership primitive", socket_path.exists())
    narrator.prove("a second cairn up is refused cleanly", rc2 == 1 and "already running" in out2)

    narrator.say(
        """
        ``cairn run`` (the inline path) is refused while a daemon owns the
        home — the daemon would fight it for the databases:
        """
    )
    rc3, out3 = _run_cli(ctx, "run", "tasks/act4_task.py")
    narrator.capture("cairn run while the daemon is live", f"exit code: {rc3}\n{out3}")
    narrator.prove("cairn run while live defers to the daemon", rc3 == 2 and "use 'cairn queue' instead" in out3)

    narrator.say(
        """
        Work goes in over the socket.  ``cairn queue`` submits at NORMAL
        priority; the daemon answers synchronously, and the mirror's
        ``list-agents`` is the public read path for the new id:
        """
    )
    rc4, out4 = _run_cli(ctx, "queue", "tasks/act4_task.py")
    narrator.capture("cairn queue (over the socket)", f"exit code: {rc4}\n{out4}")
    narrator.prove("queue accepted the task over the socket", rc4 == 0 and "submitted queue" in out4)

    # The queue reply carries no id; the mirror's list is the public read path.
    _, list_out = _run_cli(ctx, "list-agents")
    agent_id = _pick_agent_id(list_out, "act4_task")
    act4_line = next(line for line in list_out.splitlines() if "act4_task" in line)
    narrator.capture("cairn list-agents", f"{act4_line}\n... (the mirror holds every agent from this walkthrough)")

    narrator.say("The daemon runs the agent in its own process; we poll the status until it settles:")
    payload = await _wait_for_state(ctx, agent_id, wanted={"reviewing"})
    narrator.capture(
        f"cairn status {agent_id}",
        json.dumps(payload, indent=2, sort_keys=True),
    )
    narrator.prove("the agent reaches REVIEWING through the daemon", payload.get("state") == "reviewing")
    narrator.prove("the daemon computed the ground-truth changeset", payload.get("files_written") == 2)

    narrator.say("The review surface, through the CLI:")
    rc5, out5 = _run_cli(ctx, "preview", "changes", agent_id)
    narrator.capture(f"cairn preview changes {agent_id}", f"exit code: {rc5}\n{out5}")
    narrator.prove("preview shows the added and modified files", "src/added.py" in out5 and "src/main.py" in out5)

    narrator.say(
        """
        Accept applies the changeset to the real tree; undo reverses it.
        The accept added ``src/added.py`` — the exact case that used to
        break undo (the drift check read presence as drift).  Now the added
        path's accepted state is presence, so undo deletes it cleanly:
        """
    )
    rc6, out6 = _run_cli(ctx, "accept", agent_id)
    narrator.capture(f"cairn accept {agent_id}", f"exit code: {rc6}\n{out6}")
    narrator.prove("accept applied the changeset", rc6 == 0 and "file(s) written" in out6)
    narrator.prove("the added file landed in the tree", (ctx.project_root / "src" / "added.py").exists())

    rc7, out7 = _run_cli(ctx, "undo", agent_id)
    narrator.capture(f"cairn undo {agent_id}", f"exit code: {rc7}\n{out7}")
    narrator.prove("undo reversed the accept (added file deleted)", rc7 == 0 and "deletion(s) applied" in out7)
    narrator.prove("the tree is back to its pre-accept state", not (ctx.project_root / "src" / "added.py").exists())

    narrator.say(
        """
        Finally, shutting the daemon down removes the socket — ownership is
        gone, and the thin client refuses again:
        """
    )
    await _stop_daemon(state, hard=False)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and socket_path.exists():
        await asyncio.sleep(0.05)
    rc8, out8 = _run_cli(ctx, "spawn", "tasks/act4_task.py")
    narrator.capture(
        "after shutdown",
        f"socket still present : {socket_path.exists()}\nexit code (spawn): {rc8}\n{out8}",
    )
    narrator.prove("shutdown removed the control socket", not socket_path.exists())
    narrator.prove("spawn exits 2 again after shutdown", rc8 == 2)


def _extract_agent_id(out: str) -> str:
    import re

    match = re.search(r"agent-[0-9a-f]{8}", out)
    return match.group(0) if match else "agent-unknown"


def _pick_agent_id(list_out: str, task_hint: str) -> str:
    """Pick the newest agent whose task mentions ``task_hint`` from the
    ``list-agents`` lines (``id\tstate\ttask``)."""
    matches = [line for line in list_out.splitlines() if task_hint in line]
    if not matches:
        raise AssertionError(f"no agent for {task_hint!r} in list-agents output")
    return matches[-1].split("\t")[0]


# ---------------------------------------------------------------------------
# ch20 — Recovery after a daemon death (gated)
# ---------------------------------------------------------------------------


async def _ch20_recovery(narrator: Narrator, ctx: ChapterContext, state: ActIVState) -> None:
    if not ctx.options.include_recovery:
        narrator.say(
            """
            **Skipped loudly.**  This chapter kills a daemon mid-run and
            verifies recovery; it is the most likely to flake under load, so
            it is off by default.  Re-run with ``--include-recovery`` to
            exercise it.
            """
        )
        narrator.prove("ch20 skipped by default (--include-recovery required)", True)
        return

    narrator.say(
        """
        A daemon dies.  Not gracefully — ``SIGKILL``, mid-run, while an agent
        is executing.  The transport command table records the in-flight
        command, and the lifecycle store marks any agent that was mid-run
        (``GENERATING``/``EXECUTING``/``SUBMITTING``) as ``ERRORED`` on the
        next start — no zombie agents, no half-applied state:
        """
    )
    await _start_daemon(narrator, ctx, state)
    _rc, out = _run_cli(ctx, "queue", "tasks/recovery_task.py")
    narrator.capture("cairn queue (recovery task)", out)
    _, list_out = _run_cli(ctx, "list-agents")
    agent_id = _pick_agent_id(list_out, "recovery_task")
    narrator.capture(f"queued recovery task ({agent_id})", list_out)

    # Wait until the agent is mid-run (executing), then kill -9 the daemon.
    payload = await _wait_for_state(ctx, agent_id, wanted={"executing", "submitting", "generating"})
    narrator.capture(f"agent {agent_id} before the crash", f"state = {payload.get('state')}")
    await _stop_daemon(state, hard=True)

    narrator.say("The daemon is dead.  A fresh one starts over the same home and recovers:")
    await _start_daemon(narrator, ctx, state)
    recovered = await _wait_for_state(ctx, agent_id, wanted={"errored", "reviewing"}, timeout=60.0)
    narrator.capture(
        f"cairn status {agent_id} after recovery",
        json.dumps({k: recovered.get(k) for k in ("agent_id", "state", "error")}, indent=2, sort_keys=True),
    )
    narrator.prove("the interrupted agent was marked ERRORED", recovered.get("state") == "errored")
    narrator.prove("the daemon recovered and keeps serving", True)

    await _stop_daemon(state, hard=False)


CHAPTERS = [
    ("19", "Daemon + thin client", _ch19_daemon),
    ("20", "Recovery after a daemon death", _ch20_recovery),
]
