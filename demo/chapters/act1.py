"""Act I — the core loop: fixture, providers, run, untouched tree, review,
accept, undo, reject (guide §4.4).

One orchestrator per act (guide §2.5).  Chapters that consume an agent from a
previous chapter reuse it when present in ``state``, and otherwise set up
their own — so every chapter is also runnable standalone via ``--only``.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cairn.orchestrator.orchestrator import TERMINAL_STATES
from cairn.orchestrator.queue import TaskPriority
from cairn.providers.providers import FileCodeProvider, InlineCodeProvider, resolve_code_provider
from cairn.runtime import repo
from cairn.runtime.agent import AgentState
from cairn.runtime.settings import OrchestratorSettings

if TYPE_CHECKING:
    from demo.narrator import Narrator
    from demo.runner_ctx import ChapterContext

ACT_NUMERAL = "I"
ACT_TITLE = "The core loop"


@dataclass
class ActIState:
    orch: Any = field(default=None)
    #: Agent whose changes were reviewed in ch05 and accepted in ch06.
    review_agent: str | None = None


async def run(narrator: Narrator, ctx: ChapterContext, only: str | None = None) -> None:
    narrator.act(ACT_NUMERAL, ACT_TITLE)
    state = ActIState()
    state.orch = ctx.make_orchestrator(config=OrchestratorSettings(start_worker_on_init=True))
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


async def _run_agent(state: ActIState, task: str, **kwargs: Any) -> tuple[str, Any]:
    """Spawn an agent and wait for it to settle in a terminal state."""
    orch = state.orch
    agent_id = await orch.spawn_agent(task, TaskPriority.HIGH)
    record = await orch.wait_for_agent(agent_id, timeout=120.0)
    return agent_id, record


def _tree_digest(ctx: ChapterContext) -> str:
    from demo.fixture import tree_digest

    return tree_digest(ctx.project_root)


# ---------------------------------------------------------------------------
# ch01 — The fixture
# ---------------------------------------------------------------------------


async def _ch01_fixture(narrator: Narrator, ctx: ChapterContext, state: ActIState) -> None:
    narrator.say(
        """
        Every chapter drives agents against the same throwaway project under
        ``demo/out/project/``.  Nothing in this walkthrough ever touches your
        checkout — the runner aborts if ``CAIRN_PATHS_*`` is set, and every
        orchestrator asserts it is bound to the fixture before spawning
        anything.

        The fixture is a git repository whose entries each exist to make one
        line of the walkthrough true.  ``cairn`` snapshots the tree with
        ``capture_manifest`` — gitignore-aware, no symlink following — and
        that snapshot is what an agent's disposable workspace is materialized
        from:
        """
    )
    narrator.shell(["git", "-C", str(ctx.project_root), "ls-files"])

    manifest = repo.capture_manifest(ctx.project_root)
    rows: list[str] = []
    for rel in sorted(manifest.entries):
        entry = manifest.entries[rel]
        kind = entry.kind
        if kind == "symlink":
            rows.append(f"{rel:18} symlink -> {entry.link_target}")
        elif kind == "dir":
            rows.append(f"{rel:18} dir/")
        else:
            rows.append(f"{rel:18} file  {oct(entry.mode or 0)}  {entry.size}b")
    rows.append(f"{'secrets.env':18} (absent — gitignored, never materialized)")
    narrator.capture("manifest entries", "\n".join(rows))

    digest = _tree_digest(ctx)
    narrator.capture("tree digest (baseline)", digest)

    narrator.prove("gitignore filtering: secrets.env is not in the manifest", "secrets.env" not in manifest.entries)
    narrator.prove(
        "symlink preserved as a symlink (not dereferenced)",
        manifest.entry_for("link_to_main") is not None and manifest.entry_for("link_to_main").kind == "symlink",
    )
    narrator.prove(
        "empty_dir/ survives materialization (present as a dir)",
        manifest.entry_for("empty_dir") is not None and manifest.entry_for("empty_dir").kind == "dir",
    )
    narrator.prove("run.sh keeps its 0o755 mode in the manifest", (manifest.entry_for("run.sh").mode or 0) & 0o111 != 0)


# ---------------------------------------------------------------------------
# ch02 — Providers
# ---------------------------------------------------------------------------


class SlowProvider:
    """Wraps ``InlineCodeProvider`` with a deliberate delay in ``get_code``.

    Both built-in providers return instantly, so ``GENERATING`` lasts under a
    millisecond and is invisible to a lifecycle poller (guide §4.1).  This
    provider — the one ch03's agent runs under — makes the phase observable.
    """

    def __init__(self, delay: float = 0.4) -> None:
        self._inner = InlineCodeProvider()
        self.delay = delay
        self.last_context: dict[str, Any] | None = None

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        self.last_context = context
        await asyncio.sleep(self.delay)
        return await self._inner.get_code(reference, context)

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        return await self._inner.validate_code(code)


async def _ch02_providers(narrator: Narrator, ctx: ChapterContext, state: ActIState) -> None:
    narrator.say(
        """
        Code reaches an agent through a ``CodeProvider``: ``get_code`` returns
        the task source, ``validate_code`` approves it.  Two ship built in —
        ``FileCodeProvider`` (the reference is a path to a ``.py`` file) and
        ``InlineCodeProvider`` (the reference *is* the code) — and plugins
        register under the ``cairn.providers`` entry-point group.

        A provider receives a small context.  The ``workspace`` entry is a
        read-only ``ProjectView`` over the canonical tree — never a writable
        database.  Let's look at the context a real agent run produces:
        """
    )
    narrator.code(
        """
        class RecordingProvider:
            def __init__(self): self.last_context = None
            async def get_code(self, reference, context):
                self.last_context = context
                return reference
            async def validate_code(self, code):
                return True, None
        """
    )

    slow = SlowProvider()
    orch = state.orch
    orch.code_provider = slow
    task = 'write_file("src/note.txt", "provider says hi")\nsubmit_result("wrote a note", ["src/note.txt"])'
    agent_id = await orch.spawn_agent(task, TaskPriority.HIGH)
    await orch.wait_for_agent(agent_id, timeout=60.0)

    context = slow.last_context or {}
    agent_id_str = context.get("agent_id")
    project_root = context.get("project_root")
    workspace = context.get("workspace")
    narrator.capture(
        "provider context",
        "\n".join(
            [
                f"agent_id     = {agent_id_str!r}  (isinstance str: {isinstance(agent_id_str, str)})",
                f"project_root = {project_root}  (== fixture: {project_root == ctx.project_root})",
                f"workspace    = {type(workspace).__name__}  (hasattr write: {hasattr(workspace, 'write')})",
            ]
        ),
    )
    narrator.say(
        """
        Two failure modes matter.  A provider error must fail the *agent*,
        not the orchestrator — the worker keeps running.  And an unknown
        plugin name must produce the entry-point error, not a silent fallback:
        """
    )
    try:
        resolve_code_provider("nonexistent-plugin", project_root=ctx.project_root, base_path=None)
        plugin_error = "(no error raised)"
    except Exception as exc:  # noqa: BLE001 - the message is the point
        plugin_error = f"{type(exc).__name__}: {exc}"
    narrator.capture("resolve_code_provider('nonexistent-plugin')", plugin_error)

    missing = FileCodeProvider(base_path=ctx.project_root)
    orch.code_provider = missing
    bad_agent, bad_record = await _run_agent(state, "no-op")
    narrator.capture(
        "agent with a missing reference",
        f"agent_id = {bad_agent}\nstate    = {bad_record.state.value}\nerror    = {bad_record.error}",
    )
    narrator.prove(
        "a provider error lands the agent in ERRORED, not the orchestrator", bad_record.state is AgentState.ERRORED
    )
    narrator.prove("the error code is snake-cased [PROVIDER_ERROR]", "PROVIDER_ERROR" in (bad_record.error or ""))
    narrator.prove("the worker survives a provider error", orch.lifecycle is not None)
    await orch.reject_agent(bad_agent)

    # Restore the inline provider for subsequent chapters.
    orch.code_provider = InlineCodeProvider()


# ---------------------------------------------------------------------------
# ch03 — Run an agent
# ---------------------------------------------------------------------------


async def _observe_states(orch: Any, agent_id: str, timeout: float = 60.0) -> dict[str, float]:
    """Poll the lifecycle store and record when each state first appears."""
    seen: dict[str, float] = {}
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        record = await orch.lifecycle.load(agent_id)
        if record is not None:
            state = record.state.value
            if state not in seen:
                seen[state] = time.monotonic() - t0
            if record.state in TERMINAL_STATES:
                break
        await asyncio.sleep(0.005)
    return seen


async def _ch03_run_agent(narrator: Narrator, ctx: ChapterContext, state: ActIState) -> None:
    narrator.say(
        """
        An agent run is a state machine: ``QUEUED → GENERATING → EXECUTING →
        SUBMITTING → REVIEWING``.  The provider we wrote above sleeps in
        ``get_code`` and the task sleeps too, so every phase lasts long
        enough to observe.  The task is ordinary Python that runs inside
        bubblewrap against a disposable copy of the tree:
        """
    )
    narrator.code(
        """
        import time
        time.sleep(2.0)                                   # make the lifecycle visible
        write_file("src/main.py", 'print("edited by the agent")\\n')
        submit_result("updated the greeting", ["src/main.py"])
        """
    )
    slow = SlowProvider()
    state.orch.code_provider = slow
    task = (
        "import time\n"
        "time.sleep(2.0)\n"
        'write_file("src/main.py", \'print("edited by the agent")\\n\')\n'
        'submit_result("updated the greeting", ["src/main.py"])'
    )
    agent_id = await state.orch.spawn_agent(task, TaskPriority.HIGH)
    seen = await _observe_states(state.orch, agent_id)
    record = await state.orch.wait_for_agent(agent_id, timeout=60.0)

    order = ["queued", "generating", "executing", "submitting", "reviewing"]
    rows = [f"{s:11} +{seen[s]:6.3f}s" for s in order if s in seen]
    missing = [s for s in order if s not in seen]
    rows.append(f"MISSED = {missing}" if missing else "MISSED = none")
    narrator.capture(f"lifecycle states for {agent_id}", "\n".join(rows))

    narrator.prove("all five lifecycle states were observed", not missing)
    narrator.prove("the agent settles in REVIEWING", record.state is AgentState.REVIEWING)
    narrator.prove("the computed changeset recorded src/main.py (1 write)", record.files_written == 1)


# ---------------------------------------------------------------------------
# ch04 — The tree is untouched
# ---------------------------------------------------------------------------


async def _ch04_tree_untouched(narrator: Narrator, ctx: ChapterContext, state: ActIState) -> None:
    narrator.say(
        """
        Here is the headline.  Agents run against a *disposable copy* of the
        tree — copy-on-write where the filesystem supports it.  Nothing the
        agent does reaches the working tree until a human accepts.  We
        measure the tree with a content digest over the manifest:
        """
    )
    digest_before = _tree_digest(ctx)
    narrator.capture("tree digest BEFORE", digest_before)

    state.orch.code_provider = InlineCodeProvider()
    task = (
        'write_file("src/main.py", \'print("edited by the agent")\\n\')\n'
        'submit_result("updated the greeting", ["src/main.py"])'
    )
    agent_id, _record = await _run_agent(state, task)

    digest_after_run = _tree_digest(ctx)
    narrator.capture(
        "tree digest AFTER RUN",
        f"{digest_after_run}  (unchanged: {digest_after_run == digest_before})",
    )
    narrator.prove("a run does not touch the tree", digest_after_run == digest_before)

    stats = await state.orch.accept_agent(agent_id)
    narrator.capture("accept stats", json.dumps(stats, sort_keys=True))
    digest_after_accept = _tree_digest(ctx)
    narrator.capture(
        "tree digest AFTER ACCEPT",
        f"{digest_after_accept}  (changed: {digest_after_accept != digest_after_run})",
    )
    narrator.prove("accept changes the tree", digest_after_accept != digest_after_run)
    narrator.prove("the accepted file landed in the tree", (ctx.project_root / "src" / "main.py").exists())


# ---------------------------------------------------------------------------
# ch05 — The review surface
# ---------------------------------------------------------------------------


async def _ch05_review_surface(narrator: Narrator, ctx: ChapterContext, state: ActIState) -> None:
    narrator.say(
        """
        Before anything is integrated, the human sees the review surface: the
        agent's disposable workspace, diffed against the current tree.  The
        diff is the *authoritative* changeset — computed by the executor from
        what the agent actually did, not from what the agent says it did
        (chapter 09 pushes on exactly that seam).  The public way to read it
        is ``cairn preview changes``:
        """
    )
    task = (
        'write_file("src/new_module.py", "def helper_v2():\\n    return 42\\n")\n'
        'write_file("src/main.py", \'print("edited by the review agent")\\n\')\n'
        'delete_file("src/doomed.py")\n'
        'submit_result("refactor", ["src/new_module.py", "src/main.py", "src/doomed.py"])'
    )
    agent_id, _record = await _run_agent(state, task)
    state.review_agent = agent_id

    narrator.say(
        """
        The workspace itself contains the sandbox scaffolding (``.cairn/``)
        alongside the materialized tree; the diff filters it out — the review
        surface shows only what would change on accept:
        """
    )
    workdir = ctx.home / "workspaces" / agent_id
    narrator.capture("workspace top-level", ", ".join(sorted(p.name for p in workdir.iterdir())))

    preview = narrator.shell(
        [
            *ctx.cli_cmd(
                "preview", "changes", agent_id, "--cairn-home", str(ctx.home), "--project-root", str(ctx.project_root)
            ),
        ]
    )
    narrator.prove("preview lists the added module", "added      src/new_module.py" in preview)
    narrator.prove("preview lists the modified file", "modified   src/main.py" in preview)
    narrator.prove("preview lists the deleted file", "removed    src/doomed.py" in preview)
    narrator.prove("no .cairn scaffolding leaks into the diff", ".cairn" not in preview)


# ---------------------------------------------------------------------------
# ch06 — Accept
# ---------------------------------------------------------------------------


async def _ch06_accept(narrator: Narrator, ctx: ChapterContext, state: ActIState) -> None:
    narrator.say(
        """
        Accept applies the computed changeset to the real working tree —
        under a per-project integration lock, with a durable journal and a
        pre-apply snapshot so the whole thing is reversible (chapter 07).
        """
    )
    if state.review_agent is None:
        task = (
            'write_file("src/new_module.py", "def helper_v2():\\n    return 42\\n")\n'
            'write_file("src/main.py", \'print("edited by the review agent")\\n\')\n'
            'delete_file("src/doomed.py")\n'
            'submit_result("refactor", ["src/new_module.py", "src/main.py", "src/doomed.py"])'
        )
        state.review_agent, _ = await _run_agent(state, task)
    agent_id = state.review_agent

    digest_before = _tree_digest(ctx)
    stats = await state.orch.accept_agent(agent_id)
    narrator.capture("accept stats", json.dumps(stats, sort_keys=True))
    digest_after = _tree_digest(ctx)
    narrator.capture("tree digest", f"before: {digest_before}\nafter : {digest_after}")

    narrator.prove("accept applied 2 writes and 1 deletion", stats == {"files_written": 2, "files_deleted": 1})
    narrator.prove("the tree changed", digest_after != digest_before)
    narrator.prove("the deleted file is gone from the tree", not (ctx.project_root / "src" / "doomed.py").exists())
    narrator.prove("accept discards the disposable workspace", not (ctx.home / "workspaces" / agent_id).exists())


# ---------------------------------------------------------------------------
# ch07 — Undo
# ---------------------------------------------------------------------------


async def _ch07_undo(narrator: Narrator, ctx: ChapterContext, state: ActIState) -> None:
    narrator.say(
        """
        Every accept is reversible.  Before applying, cairn snapshots the
        pre-accept content of every touched path into ``bin.db`` under
        ``undo/{agent_id}/``; ``cairn undo`` restores the tree to that state.
        The interesting case is an accept that *created* a file — undoing it
        means deleting it, and the drift check must read the accepted state
        (presence) correctly:
        """
    )
    state.orch.code_provider = InlineCodeProvider()
    task = (
        'write_file("src/brand_new.py", "def shiny():\\n    return True\\n")\n'
        'submit_result("added a module", ["src/brand_new.py"])'
    )
    agent_id, _record = await _run_agent(state, task)
    stats = await state.orch.accept_agent(agent_id)
    narrator.capture(
        f"accept {agent_id}",
        f"stats        = {json.dumps(stats, sort_keys=True)}\n"
        f"brand_new.py exists after accept: {(ctx.project_root / 'src' / 'brand_new.py').exists()}",
    )

    undo_stats = await state.orch.undo_accept(agent_id)
    narrator.capture(
        f"undo {agent_id}",
        f"stats                  = {json.dumps(undo_stats, sort_keys=True)}\n"
        f"brand_new.py exists    : {(ctx.project_root / 'src' / 'brand_new.py').exists()}\n"
        f"src/main.py untouched  : {(ctx.project_root / 'src' / 'main.py').exists()}",
    )
    narrator.prove("undo restored the tree (added file removed)", undo_stats == {"restored": 0, "deleted": 1})

    narrator.say(
        """
        Undo is fail-closed: it validates that the accepted state is still
        present, and refuses (``UNDO_STALE_BASE``) rather than silently
        overwriting a later human edit:
        """
    )
    task2 = (
        'write_file("src/human_edit_target.py", "agent version\\n")\n'
        'submit_result("added a file", ["src/human_edit_target.py"])'
    )
    agent2, _ = await _run_agent(state, task2)
    await state.orch.accept_agent(agent2)
    (ctx.project_root / "src" / "human_edit_target.py").write_text("human edited after accept\n", encoding="utf-8")
    from cairn.core.exceptions import WorkspaceMergeError

    try:
        await state.orch.undo_accept(agent2)
        refusal = "(undo unexpectedly succeeded)"
    except WorkspaceMergeError as exc:
        refusal = f"{exc.error_code}  stale_paths={exc.context.get('stale_paths')}"
    narrator.capture("undo over a human edit", refusal)
    narrator.prove("undo refuses on drift (UNDO_STALE_BASE)", refusal.startswith("UNDO_STALE_BASE"))
    narrator.prove(
        "the human edit survives",
        (ctx.project_root / "src" / "human_edit_target.py").read_text() == "human edited after accept\n",
    )


# ---------------------------------------------------------------------------
# ch08 — Reject
# ---------------------------------------------------------------------------


async def _ch08_reject(narrator: Narrator, ctx: ChapterContext, state: ActIState) -> None:
    narrator.say(
        """
        Reject is the other side of review: the disposable workspace is
        discarded and the tree is untouched.  Nothing was integrated, so
        there is nothing to undo.
        """
    )
    digest_before = _tree_digest(ctx)
    state.orch.code_provider = InlineCodeProvider()
    task = (
        'write_file("src/never_land.py", "print(1)\\n")\n'
        'submit_result("work that should not land", ["src/never_land.py"])'
    )
    agent_id, record = await _run_agent(state, task)
    narrator.capture(
        f"agent {agent_id} in review",
        f"state = {record.state.value}\nworkdir exists: {(ctx.home / 'workspaces' / agent_id).exists()}",
    )

    await state.orch.reject_agent(agent_id)
    digest_after = _tree_digest(ctx)
    narrator.capture(
        f"after reject {agent_id}",
        f"workdir exists     : {(ctx.home / 'workspaces' / agent_id).exists()}\n"
        f"tree digest changed: {digest_after != digest_before}\n"
        f"never_land.py      : {(ctx.project_root / 'src' / 'never_land.py').exists()}",
    )
    narrator.prove("reject discards the workspace", not (ctx.home / "workspaces" / agent_id).exists())
    narrator.prove("reject leaves the tree untouched", digest_after == digest_before)


CHAPTERS = [
    ("01", "The fixture", _ch01_fixture),
    ("02", "Providers", _ch02_providers),
    ("03", "Run an agent", _ch03_run_agent),
    ("04", "The tree is untouched", _ch04_tree_untouched),
    ("05", "The review surface", _ch05_review_surface),
    ("06", "Accept", _ch06_accept),
    ("07", "Undo", _ch07_undo),
    ("08", "Reject", _ch08_reject),
]
