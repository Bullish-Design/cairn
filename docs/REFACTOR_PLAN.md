# Cairn Refactoring Guide

Step-by-step implementation plan for the issues raised in the 2026-08-04 code
review.  Each step is independently landable and ends with a concrete
verification.  Work top to bottom: later phases assume the architecture
established in Phase 1.

**Status legend:** each step has a `Done when` line.  Treat it as the
acceptance criterion — if you can't demonstrate it, the step isn't finished.

---

## Contents

- [Phase 0 — Restore a working environment](#phase-0--restore-a-working-environment)
- [Phase 1 — Make the documented workflow actually work](#phase-1--make-the-documented-workflow-actually-work)
- [Phase 2 — Make the review gate trustworthy](#phase-2--make-the-review-gate-trustworthy)
- [Phase 3 — Close the containment gaps](#phase-3--close-the-containment-gaps)
- [Phase 4 — Robustness](#phase-4--robustness)
- [Phase 5 — Hygiene](#phase-5--hygiene)
- [Appendix A — Three decisions to make before starting](#appendix-a--three-decisions-to-make-before-starting)

---

## Phase 0 — Restore a working environment

### P0.1 Rebuild the toolchain

The `.devenv/state/venv` symlinks point at garbage-collected Nix store paths,
so nothing currently runs.

```bash
cd /home/andrew/Documents/Projects/cairn
devenv shell          # re-realises the store closure
uv sync --all-extras  # rebuild the venv
```

**Done when:** `python -c "import fsdantic; print(fsdantic.__version__)"` works
inside the shell.

### P0.2 Capture a baseline

```bash
devenv test 2>&1 | tee /tmp/cairn-baseline.txt
```

You need to know which tests were already failing before you start changing
things.  Several steps below intentionally *break* existing tests, and you
need to be able to tell those apart from pre-existing failures.

**Done when:** you have a recorded pass/fail list for all 191 tests.

### P0.3 Pin the fsdantic API surface you depend on

The vendored copy at `.context/fsdantic/` is what this guide's code was written
against.  Confirm it matches the installed version:

```bash
python - <<'PY'
from fsdantic import MergeStrategy
from fsdantic.models import BatchResult, BatchItemResult, FileStats
print([s.value for s in MergeStrategy])          # overwrite/preserve/error/callback
print(sorted(BatchItemResult.model_fields))      # error,index,key_or_path,ok,value
print(sorted(FileStats.model_fields))            # is_directory,is_file,mtime,size
PY
```

**Done when:** all three lines print without error and match the comments.

---

## Phase 1 — Make the documented workflow actually work

Three defects mean the README quickstart does not do what it says.  Fix them
in this order; P1.4 depends on P1.1 having shrunk the file set.

### P1.1 Widen the watcher's ignore set

**Why:** the ignore list is `[".agentfs", ".git", ".jj", "__pycache__",
"node_modules"]` (`src/cairn/watcher/watcher.py:20`).  In this repository that
leaves 3,146 files / 362 MB in scope — `.devenv`, `.venv`, `.ruff_cache`,
`.mypy_cache`, `.pytest_cache`.  This must be fixed *before* P1.4, or the
initial sync will try to import all of it.

**Files:** `src/cairn/watcher/watcher.py`, `src/cairn/runtime/settings.py`

Replace the hand-rolled `should_ignore` with a `watchfiles.DefaultFilter`
subclass.  `watchfiles` is already a dependency, `DefaultFilter` already
excludes the common junk, and passing it to `awatch` means the filtering
happens in Rust instead of after the fact in Python.

`DefaultFilter.ignore_dirs` already covers `__pycache__`, `.git`, `.hg`, `.svn`,
`.tox`, `.venv`, `.idea`, `node_modules`, `.mypy_cache`, `.pytest_cache` and
`.hypothesis`, and its `ignore_entity_patterns` already drop `*.pyc`, editor
swapfiles and `.DS_Store`.  The list below is only the delta Cairn needs.

```python
# src/cairn/watcher/watcher.py
from watchfiles import Change, DefaultFilter, awatch

EXTRA_IGNORE_DIRS: tuple[str, ...] = (
    ".agentfs", ".jj", ".devenv", ".direnv", "venv",
    ".ruff_cache", ".coverage", "htmlcov",
    "dist", "build", "target", ".eggs",
)

DEFAULT_IGNORE_SUFFIXES: tuple[str, ...] = (
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
    ".so", ".dylib", ".dll", ".o", ".a", ".pyc", ".pyo",
)


class ProjectFilter(DefaultFilter):
    """DefaultFilter plus Cairn's own exclusions."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        super().__init__(ignore_dirs=(*DefaultFilter.ignore_dirs, *EXTRA_IGNORE_DIRS))

    def __call__(self, change: Change, path: str) -> bool:
        if not super().__call__(change, path):
            return False
        return self.allows(Path(path))

    def allows(self, path: Path) -> bool:
        """Predicate shared by the watcher and the initial sync.

        Note this is a *name*-based decision only - it must stay cheap enough
        to run on every filesystem event.  Size is checked separately, at the
        point of reading, because a path's size changes over time.
        """
        if path.suffix in DEFAULT_IGNORE_SUFFIXES:
            return False
        try:
            rel = path.relative_to(self.project_root)
        except ValueError:
            return False
        # DefaultFilter matches ancestor components of the absolute path too;
        # re-check against the project-relative parts so a project living under
        # a directory named e.g. "build" is not excluded wholesale.
        return not any(part in EXTRA_IGNORE_DIRS for part in rel.parts)
```

`FileWatcher.__init__` takes the filter; `watch()` passes it to `awatch`:

```python
    def __init__(self, project_root: Path, workspace: Workspace,
                 *, max_file_bytes: int = DEFAULT_MAX_SYNC_FILE_BYTES) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = workspace
        self.max_file_bytes = max_file_bytes
        self.filter = ProjectFilter(self.project_root)

    async def watch(self) -> None:
        async for changes in awatch(self.project_root, watch_filter=self.filter):
            for change_type, path_str in changes:
                await self.handle_change(change_type, Path(path_str))
```

The size cap has to be enforced on the live path as well, not just during the
initial sync — otherwise a single large build artifact still lands in stable:

```python
    async def handle_change(self, change_type: Change, path: Path) -> None:
        if not self.filter.allows(path) or path.is_dir():
            return
        rel_path = path.relative_to(self.project_root).as_posix()

        if change_type == Change.deleted:
            if await self.workspace.files.exists(rel_path):
                await self.workspace.files.remove(rel_path)
            return

        try:
            if path.stat().st_size > self.max_file_bytes:
                logger.debug("Skipping oversized file", extra={"path": rel_path})
                return
        except OSError:
            return                       # vanished between event and stat

        content = await asyncio.to_thread(path.read_bytes)   # see P4.4
        await self.workspace.files.write(rel_path, content, mode="binary")
```

Add `max_sync_file_bytes: int = 5 * 1024 * 1024` and an
`extra_ignore_dirs: list[str] = []` to `OrchestratorSettings` so this is
tunable per project without editing code.

**Verify:** add `tests/cairn/test_watcher.py::test_filter_excludes_build_dirs`
asserting `.venv/lib/x.py`, `foo.db-wal` and a 6 MB file are all excluded, and
that `src/main.py` is not.  Then re-run the measurement:

```bash
python - <<'PY'
from pathlib import Path
from cairn.watcher.watcher import ProjectFilter
root = Path(".").resolve()
f = ProjectFilter(root)
n = sum(1 for p in root.rglob("*") if p.is_file() and not p.is_symlink() and f.allows(p))
print("in scope:", n)
PY
```

**Done when:** the count drops from ~3,146 to the low hundreds.  (Measured on
this repository before the change: 3,146 files / 362 MB, almost all of it
`.devenv`, `.venv` and the various `*_cache` directories.)

### P1.2 Add `initial_sync` to the watcher

**Why:** nothing ever imports the project into `stable`
(`src/cairn/orchestrator/orchestrator.py:105-110` opens the DB; `watcher.py:22-25`
is delta-only).  Agents materialize an empty tree.  The tests already hand-roll
this function at `tests/cairn/test_materialize_live_fixture.py:48-54`.

**Files:** `src/cairn/watcher/watcher.py`

```python
import asyncio
from dataclasses import dataclass

_SEED_BATCH = 128


@dataclass(frozen=True)
class SyncStats:
    written: int
    skipped_large: int
    failed: int


class FileWatcher:
    ...

    def _collect(self) -> tuple[list[Path], int]:
        """Walk the project tree (blocking — call via to_thread)."""
        paths: list[Path] = []
        skipped = 0
        for path in sorted(self.project_root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if not self.filter.allows(path):
                continue
            try:
                if path.stat().st_size > self.max_file_bytes:
                    skipped += 1
                    continue
            except OSError:
                continue
            paths.append(path)
        return paths, skipped

    @staticmethod
    def _read_batch(root: Path, chunk: list[Path]) -> list[tuple[str, bytes]]:
        items: list[tuple[str, bytes]] = []
        for path in chunk:
            try:
                items.append((path.relative_to(root).as_posix(), path.read_bytes()))
            except OSError:
                continue
        return items

    async def initial_sync(self) -> SyncStats:
        """Mirror the current project tree into the stable workspace.

        Runs once at orchestrator startup so agents materialize a workspace
        that reflects the project, not an empty tree.
        """
        paths, skipped = await asyncio.to_thread(self._collect)
        written = failed = 0
        for start in range(0, len(paths), _SEED_BATCH):
            chunk = paths[start : start + _SEED_BATCH]
            items = await asyncio.to_thread(self._read_batch, self.project_root, chunk)
            result = await self.workspace.files.write_many(items, mode="binary")
            for item in result.items:
                if item.ok:
                    written += 1
                else:
                    failed += 1
                    logger.warning(
                        "Initial sync failed for path",
                        extra={"path": item.key_or_path, "error": item.error},
                    )
        stats = SyncStats(written=written, skipped_large=skipped, failed=failed)
        logger.info("Initial project sync complete", extra=stats.__dict__)
        return stats
```

Note the batching and the `to_thread` reads: this must not hold the whole tree
in memory nor block the event loop (see P4.4).

Call it from `initialize()`, **before** the worker loop starts, so no agent can
materialize a half-seeded stable:

```python
# src/cairn/orchestrator/orchestrator.py, in initialize()
        self.watcher = FileWatcher(self.project_root, self.stable, ...)
        ...
        if self.config.sync_project_on_start:
            await self.watcher.initial_sync()

        await self.recover_from_lifecycle_store()
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
```

Add `sync_project_on_start: bool = True` to `OrchestratorSettings`.  Tests that
want an empty stable set it to `False`.

**Verify:** new test — create a temp project with `src/a.py`, build an
orchestrator, assert `await orch.stable.files.read("src/a.py") == ...`.  Also
convert `tests/cairn/test_materialize_live_fixture.py::_seed_from_fixtures` to
call `FileWatcher(...).initial_sync()` so the test helper and production share
one implementation.

**Done when:** a fresh `cairn up` in a project directory produces a `stable.db`
containing the project's source files, and a spawned agent's `read_file()` can
see them.

### P1.3 Introduce a daemon pidfile

**Why:** everything in P1.4/P1.5 needs to know whether a daemon owns the
databases.  It also makes the "two processes writing the same SQLite files"
situation detectable instead of silent.

**Files:** new `src/cairn/orchestrator/daemon.py`

```python
"""Daemon liveness tracking via a pidfile under $CAIRN_HOME/state."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PIDFILE_NAME = "orchestrator.pid"


def pidfile_path(cairn_home: Path) -> Path:
    return Path(cairn_home) / "state" / PIDFILE_NAME


def read_daemon_pid(cairn_home: Path) -> int | None:
    """Return the live daemon's pid, or None if no daemon is running.

    A stale pidfile (process gone) is treated as no daemon.
    """
    path = pidfile_path(cairn_home)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        os.kill(pid, 0)          # signal 0 = liveness probe only
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid               # exists, owned by another user
    return pid


@contextmanager
def daemon_pidfile(cairn_home: Path) -> Iterator[Path]:
    """Claim the daemon pidfile for the duration of the block.

    Raises RuntimeError if another live daemon already holds it.
    """
    path = pidfile_path(cairn_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_daemon_pid(cairn_home)
    if existing is not None and existing != os.getpid():
        raise RuntimeError(f"A Cairn daemon is already running (pid {existing})")
    tmp = path.with_suffix(".pid.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    tmp.replace(path)            # atomic
    try:
        yield path
    finally:
        try:
            if read_daemon_pid(cairn_home) == os.getpid():
                path.unlink(missing_ok=True)
        except OSError:
            pass
```

Wrap `_run_up` in it:

```python
# src/cairn/cli/cli.py
async def _run_up(args) -> int:
    path_settings, orchestrator_settings, executor_settings = _resolve_settings(args)
    cairn_home = Path(path_settings.cairn_home or Path.home() / ".cairn").expanduser()
    with daemon_pidfile(cairn_home):
        orchestrator = CairnOrchestrator(...)
        await orchestrator.initialize()
        try:
            await orchestrator.run()
        finally:
            await orchestrator.shutdown()
    return 0
```

**Verify:** test that a second `daemon_pidfile()` raises while the first is
held, and that a pidfile naming a dead pid is treated as absent.

**Done when:** `cairn up` twice in the same `CAIRN_HOME` refuses the second
with a clear message.

### P1.4 Make the CLI a thin client (the core architectural fix)

**Why:** this fixes all three Phase-1 blockers at once.  Today every
subcommand — including `status` and `list-agents` — constructs a full
orchestrator, calls `initialize()` (which recovers records, re-enqueues QUEUED
agents and starts a worker), then gets killed by `asyncio.run` teardown
(`src/cairn/cli/cli.py:105-113`, `orchestrator.py:116-117,173-174`).  So
`spawn` never executes anything, and `status` can start executing agents.
Meanwhile the signal transport documented at `docs/SPEC.md:295` has a live
consumer and zero producers.

The new model: **the daemon owns the databases.  The CLI writes signals and
reads read-only.**

#### P1.4a Add a signal writer

```python
# src/cairn/orchestrator/signals.py

import json
import os
import time
import uuid
from pathlib import Path

from cairn.cli.commands import CairnCommand


def write_signal(cairn_home: Path, command: CairnCommand) -> Path:
    """Atomically drop a command signal file for the daemon to pick up.

    Written to a temp name and renamed so the watcher never observes a
    partially-written file.
    """
    signals_dir = Path(cairn_home) / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    payload = command.to_payload()
    payload["signal_id"] = uuid.uuid4().hex
    payload["issued_at"] = time.time()
    payload["issued_by_pid"] = os.getpid()

    target = signals_dir / f"{command.type.value}-{payload['signal_id']}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target
```

Note `.json.tmp` does not match the `*.json` watch filter or the `*.json` glob,
so the rename is the only thing the daemon ever sees.

#### P1.4b Read-only query paths

`status` and `list-agents` must never construct an orchestrator.  Add:

```python
# src/cairn/orchestrator/lifecycle.py

from contextlib import asynccontextmanager

@asynccontextmanager
async def open_lifecycle_readonly(agentfs_dir: Path):
    """Open the lifecycle store read-only (safe alongside a running daemon)."""
    from cairn.runtime.workspace_manager import open_workspace

    bin_db = Path(agentfs_dir) / "bin.db"
    if not bin_db.exists():
        raise AgentNotFoundError(
            "No Cairn state found - has the orchestrator ever run?",
            error_code="LIFECYCLE_STORE_MISSING",
        )
    workspace = await open_workspace(bin_db, readonly=True)
    try:
        yield LifecycleStore(workspace)
    finally:
        await workspace.close()
```

Rewrite `_run_status` / `_run_list_agents` against it:

```python
async def _run_list_agents(args) -> int:
    path_settings, *_ = _resolve_settings(args)
    agentfs_dir = (path_settings.project_root or Path(".")).resolve() / ".agentfs"
    async with open_lifecycle_readonly(agentfs_dir) as store:
        records = await store.list_all()
    if not records:
        print("No agents")
        return 0
    for record in sorted(records, key=lambda r: r.agent_id):
        print(f"{record.agent_id}\t{record.state.value}\t{record.task}")
    return 0
```

#### P1.4c Mutating commands go through signals

```python
async def _run_queue(args) -> int:
    return await _dispatch_mutation(
        args,
        parse_command_payload("queue", {"task": args.task, "priority": int(TaskPriority.NORMAL)}),
    )


async def _dispatch_mutation(args, command: CairnCommand) -> int:
    path_settings, *_ = _resolve_settings(args)
    cairn_home = Path(path_settings.cairn_home or Path.home() / ".cairn").expanduser()

    if read_daemon_pid(cairn_home) is None:
        print(
            "No Cairn daemon is running.\n"
            "  Start one with:  cairn up\n"
            "  Or run this task inline with:  cairn run <task>",
            file=sys.stderr,
        )
        return 2

    path = write_signal(cairn_home, command)
    print(f"submitted {command.type.value} ({path.name})")
    return 0
```

Delete `CairnCommandClient` entirely — it is the mechanism that caused the bug.

#### P1.4d Add an explicit inline mode

Users still need a way to run a one-shot task without a daemon.  Make it
explicit rather than accidental:

```python
async def _run_inline(args) -> int:
    """Run a single task to completion in this process, then exit."""
    path_settings, orchestrator_settings, executor_settings = _resolve_settings(args)
    cairn_home = Path(path_settings.cairn_home or Path.home() / ".cairn").expanduser()
    if read_daemon_pid(cairn_home) is not None:
        print("A daemon is running; use 'cairn queue' instead.", file=sys.stderr)
        return 2

    orchestrator = CairnOrchestrator(...)
    await orchestrator.initialize()
    try:
        agent_id = await orchestrator.spawn_agent(args.task, TaskPriority.HIGH)
        record = await orchestrator.wait_for_agent(agent_id, timeout=args.timeout)
        print(json.dumps({"agent_id": agent_id, "state": record.state.value}, indent=2))
        return 0 if record.state is AgentState.REVIEWING else 1
    finally:
        await orchestrator.shutdown()
```

Add the supporting method:

```python
# src/cairn/orchestrator/orchestrator.py
TERMINAL_STATES = {AgentState.REVIEWING, AgentState.ACCEPTED,
                   AgentState.REJECTED, AgentState.ERRORED}

async def wait_for_agent(self, agent_id: str, *, timeout: float = 300.0,
                         poll_interval: float = 0.05) -> LifecycleRecord:
    """Block until the agent reaches a terminal state."""
    deadline = time.monotonic() + timeout
    while True:
        record = await self.lifecycle.load(agent_id)
        if record is not None and record.state in TERMINAL_STATES:
            return record
        if time.monotonic() >= deadline:
            raise CairnTimeoutError(
                f"Agent {agent_id} did not settle within {timeout}s",
                error_code="AGENT_WAIT_TIMEOUT",
                context={"agent_id": agent_id, "timeout_seconds": timeout},
            )
        await asyncio.sleep(poll_interval)
```

#### P1.4e Give accept/reject synchronous feedback

The CLI drops a signal, then polls the read-only lifecycle store until the
state settles, so the user still sees a result:

```python
async def _run_accept(args) -> int:
    command = parse_command_payload("accept", {"agent_id": args.agent_id})
    rc = await _dispatch_mutation(args, command)
    if rc != 0:
        return rc
    record = await _poll_until(args, args.agent_id,
                              {AgentState.ACCEPTED, AgentState.ERRORED},
                              timeout=args.timeout)
    if record.state is AgentState.ERRORED:
        print(f"accept failed: {record.error}", file=sys.stderr)
        return 1
    stats = record.accept_stats or {}
    print(f"accepted {args.agent_id}: "
          f"{stats.get('files_merged', 0)} file(s) merged, "
          f"{stats.get('tombstones_applied', 0)} deletion(s) applied")
    return 0
```

This needs a new field on the record — add it in P2.1's model change:

```python
class LifecycleRecord(VersionedKVRecord):
    ...
    accept_stats: dict[str, int] | None = None
```

and have `accept_agent` populate it before saving.

**Verify:** the test that currently does not exist —

```python
# tests/cairn/integration/test_cli_daemon.py
@pytest.mark.integration
async def test_spawn_reaches_running_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_PATHS_PROJECT_ROOT", str(project))
    monkeypatch.setenv("CAIRN_PATHS_CAIRN_HOME", str(home))

    daemon = asyncio.create_task(asyncio.to_thread(cairn.cli.cli.main, ["up"]))
    await _wait_for_pidfile(home)

    assert cairn.cli.cli.main(["queue", "scripts/task.py"]) == 0

    record = await _wait_for_state(home, timeout=30)
    assert record.state is AgentState.REVIEWING   # today it stays QUEUED
```

Also add `tests/cairn/test_cli.py::test_status_does_not_start_a_worker`
asserting that running `status` leaves no `orchestrator.json` mtime change and
creates no `agent-*.db`.

**Done when:** the README quickstart works end to end — `cairn up` in one
terminal, `cairn spawn scripts/foo.py` in another, and the agent reaches
`reviewing` without restarting the daemon.

### P1.5 Fix the signal handler's race, deletion-on-failure, and re-entrancy

**Why:** `process_signals_once()` runs before `awatch` starts, so a file
created in that gap is never seen (`signals.py:47-57`); failures delete the
signal anyway (`signals.py:81-88`), so a failed accept vanishes with only a log
line; and `Change.modified` can re-process a file.

**Files:** `src/cairn/orchestrator/signals.py`

Three changes:

1. **Claim before processing.**  Rename the file to `*.processing` first.  The
   rename is atomic, so two observers cannot both claim it.

```python
    async def _process_signal_path(self, signal_file: Path) -> None:
        claimed = signal_file.with_suffix(".processing")
        try:
            signal_file.rename(claimed)      # atomic claim
        except FileNotFoundError:
            return                            # someone else got it
        except OSError as exc:
            logger.warning("Could not claim signal", extra={"file": str(signal_file), "error": str(exc)})
            return

        try:
            command = self._parse_signal_file(claimed)
            if command is None:
                self._quarantine(claimed, "unparseable signal payload")
                return
            await self._dispatch(command)
        except Exception as exc:
            logger.exception("Error processing signal", extra={"file": str(claimed)})
            self._quarantine(claimed, str(exc))
        else:
            claimed.unlink(missing_ok=True)

    def _quarantine(self, path: Path, reason: str) -> None:
        """Move a failed signal to signals/failed/ with an error sidecar."""
        failed_dir = self.signals_dir / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        target = failed_dir / path.with_suffix(".json").name
        try:
            path.replace(target)
            target.with_suffix(".error.txt").write_text(reason, encoding="utf-8")
        except OSError:
            logger.exception("Could not quarantine signal", extra={"file": str(path)})
```

2. **Add a periodic sweep as a backstop** so the awatch startup gap cannot lose
   a signal:

```python
    async def watch(self) -> None:
        if not self.enable_polling:
            return
        self.signals_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(self._watch_events(), self._sweep_loop())

    async def _sweep_loop(self) -> None:
        """Backstop scan; awatch provides latency, this provides guarantees."""
        while True:
            await self.process_signals_once()
            await asyncio.sleep(SIGNAL_SWEEP_INTERVAL_SECONDS)
```

Use the existing but currently unused
`SIGNAL_POLL_INTERVAL_SECONDS` constant (rename it
`SIGNAL_SWEEP_INTERVAL_SECONDS`, default 1.0).

3. **Exclude the working suffixes** from both the glob and the watch filter:
   `_detect_signal_files` should keep globbing `*.json` (which now excludes
   `.processing`, `.json.tmp` and the `failed/` subdirectory since `glob` is
   non-recursive).

**Verify:** a test that writes a signal whose dispatch raises, and asserts the
file lands in `signals/failed/` with a sidecar rather than disappearing.  A
second test that claims a file from two concurrent `_process_signal_path` calls
and asserts `_dispatch` ran exactly once.

**Done when:** failed signals are inspectable on disk, and a signal written
during daemon startup is still processed.

### P1.6 Replace `KeyError` with a typed error

**Why:** `cairn status <unknown-id>` catches `ValueError` (`cli.py:162-166`)
but the orchestrator raises `KeyError` (`orchestrator.py:242,246`), so the
friendly branch is dead and the user gets a traceback.  Same at
`typer_cli.py:546`.

**Files:** `src/cairn/core/exceptions.py`, `orchestrator.py`, both CLIs

```python
# core/exceptions.py
class AgentNotFoundError(FatalError, KeyError):
    """No agent with the requested id exists."""
```

Inheriting `KeyError` keeps any existing `except KeyError` callers working
during the transition.  Replace all three `raise KeyError(f"Unknown agent_id: ...")`
sites with:

```python
raise AgentNotFoundError(
    f"Unknown agent_id: {agent_id}",
    error_code="AGENT_NOT_FOUND",
    context={"agent_id": agent_id},
)
```

and catch `AgentNotFoundError` in both CLIs.

**Verify:** `test_status_unknown_agent_exits_1` asserting exit code 1 and a
message on stderr, not a traceback.

**Done when:** `cairn status agent-nope` prints `Unknown agent: agent-nope`
and exits 1.

---

## Phase 2 — Make the review gate trustworthy

### P2.1 Stop discarding the real changeset and the run log

**Why:** `BwrapExecutor` computes the actual changeset and captures the log,
and the orchestrator throws both away:

```python
result = await self._execute_code(ctx, generated)
ctx.execution_result = {"status": "complete"}   # orchestrator.py:488
ctx.submission = result.submission
```

`ctx.execution_result` is a constant that nothing reads.  What the human
reviews is `submission["changed_files"]` — a file the agent wrote about itself.

**Files:** `src/cairn/runtime/sandbox/sandbox.py`, `orchestrator/lifecycle.py`,
`orchestrator/orchestrator.py`, both CLIs

Extend the result the executor already produces:

```python
# runtime/sandbox/sandbox.py
@dataclass
class SandboxResult:
    submission: SubmissionData | None
    changes: dict[str, list[str]] = field(default_factory=lambda: {"written": [], "deleted": []})
    log: str = ""
    base_hashes: dict[str, str] = field(default_factory=dict)   # for P2.2
    exit_code: int = 0
```

In `run()`, populate `base_hashes` from the baseline you already computed — no
extra work:

```python
        written, deleted = self._diff_snapshot(workdir, baseline)
        touched = [rel for rel, _ in written] + deleted
        base_hashes = {rel: baseline[rel] for rel in touched if rel in baseline}
        await self._reimport(written, deleted)
        ...
        return SandboxResult(
            submission=submission,
            changes={"written": [rel for rel, _ in written], "deleted": deleted},
            log=run_log,
            base_hashes=base_hashes,
            exit_code=proc.returncode or 0,
        )
```

Persist a run record next to the submission record:

```python
# orchestrator/lifecycle.py
RUN_KEY = "run"


class RunRecord(VersionedKVRecord):
    """Ground truth about what the sandbox actually did."""

    agent_id: str
    written: list[str] = []
    deleted: list[str] = []
    base_hashes: dict[str, str] = {}
    log: str = ""
    exit_code: int = 0
```

Add summary fields to `LifecycleRecord` so `list-agents` can show them without
opening the agent workspace:

```python
class LifecycleRecord(VersionedKVRecord):
    ...
    files_written: int = 0
    files_deleted: int = 0
    claim_mismatch: bool = False
    accept_stats: dict[str, int] | None = None
```

Then in the orchestrator, replace the discard:

```python
    async def _execute_agent_lifecycle(self, ctx: AgentContext) -> None:
        ...
        result = await self._execute_code(ctx, generated)
        ctx.submission = result.submission
        await self._record_run(ctx, result)
        ...

    async def _record_run(self, ctx: AgentContext, result: SandboxResult) -> None:
        """Persist the sandbox's ground-truth changeset for human review."""
        agent_fs = ctx.agent_fs or await self._get_agent_workspace(ctx)
        record = RunRecord(
            agent_id=ctx.agent_id,
            written=result.changes["written"],
            deleted=result.changes["deleted"],
            base_hashes=result.base_hashes,
            log=result.log[-MAX_STORED_LOG_BYTES:],
            exit_code=result.exit_code,
        )
        repo = agent_fs.kv.repository(prefix="", model_type=RunRecord)
        await repo.save(RUN_KEY, record)

        ctx.files_written = len(record.written)
        ctx.files_deleted = len(record.deleted)
        claimed = set((result.submission or {}).get("changed_files", []))
        actual = set(record.written) | set(record.deleted)
        ctx.claim_mismatch = bool(claimed) and claimed != actual
```

Mirror the three fields into `_apply_lifecycle_update` and the
`LifecycleRecord(...)` construction in `_save_lifecycle_record`, and add them to
`AgentContext`.

Surface it.  `cairn status` should print the actual changeset, and mark the
divergence loudly:

```
agent-1a2b3c4d  reviewing
  agent claims : src/main.py
  actually wrote: src/main.py, src/util.py, .github/workflows/ci.yml
  ! the agent's self-report does not match what it did
```

**Verify:** a test where `task.py` writes two files but calls
`submit_result(summary=..., changed_files=["a.txt"])`, asserting
`record.claim_mismatch is True` and that the run record lists both files.

**Done when:** `cairn status` shows the sandbox-observed changeset, and a
lying agent is flagged.

### P2.2 Add a staleness check to accept

**Why:** `accept_agent` does `merge(..., strategy=MergeStrategy.OVERWRITE)`
(`orchestrator.py:347`) and checks `merge_result.errors`.  Reading fsdantic's
`overlay.py:309-331`: under `OVERWRITE` the conflict is detected and then
discarded — it is appended to `conflicts` only under `PRESERVE`/`CALLBACK`, and
to `errors` only under `ERROR`.  So the check at `orchestrator.py:348` can
never fire for a content conflict.  If you edit a file while the agent is
working on it, accept silently overwrites your edit and reports success.

`MergeStrategy.ERROR` is *not* the fix — fsdantic treats every modification of
an existing file as a conflict, so it would reject every normal accept.  The
fix is a base-version comparison, using the `base_hashes` captured in P2.1.

**Files:** `src/cairn/orchestrator/orchestrator.py`

```python
    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def _detect_stale_paths(self, run: RunRecord) -> list[str]:
        """Paths whose content in stable changed after the agent read them.

        base_hashes holds the digest each touched path had in the materialized
        workspace at run start.  If stable's current content no longer matches,
        something (you, the watcher, another agent) changed it in the meantime
        and an OVERWRITE merge would silently discard that change.
        """
        stale: list[str] = []
        for rel, base_digest in run.base_hashes.items():
            try:
                current = await self.stable.files.read(rel, mode="binary")
            except Exception:
                continue          # absent now: deletion is handled by tombstones
            if self._sha256_bytes(current) != base_digest:
                stale.append(rel)
        return sorted(stale)
```

Wire it into `accept_agent`, which gains a `force` flag:

```python
    async def accept_agent(self, agent_id: str, *, force: bool = False) -> dict[str, int]:
        ctx = self._get_agent(agent_id)
        if ctx.state is not AgentState.REVIEWING:
            raise ValueError(f"Agent {agent_id} not in reviewing state")
        ...
        agent_fs = await self._get_agent_workspace(ctx)
        run = await self._load_run_record(agent_fs)

        if run is not None and not force:
            stale = await self._detect_stale_paths(run)
            if stale:
                raise WorkspaceMergeError(
                    format_agent_error(
                        "Stable changed since this agent started; accepting would "
                        "discard those changes",
                        agent_id=agent_id,
                        state=ctx.state.value,
                        stale_paths=stale,
                    ),
                    error_code="ACCEPT_STALE_BASE",
                    context={"agent_id": agent_id, "stale_paths": stale},
                )

        await self._snapshot_for_undo(ctx, run)      # P2.3
        merge_result = await self.stable.overlay.merge(agent_fs, strategy=MergeStrategy.OVERWRITE)
        ...
```

Add `--force` to both CLIs' accept commands, and make the error message tell
the user their options:

```
accept refused: stable changed since agent-1a2b3c4d started
  src/main.py
Re-run the agent against current stable, or re-accept with --force to
overwrite these paths.
```

**Verify:** integration test — seed stable, spawn an agent that rewrites
`a.txt`, wait for `reviewing`, then write a *different* `a.txt` into stable
directly, then assert `accept_agent` raises `ACCEPT_STALE_BASE` and that
`accept_agent(force=True)` succeeds.

**Done when:** the concurrent-edit scenario is refused by default instead of
silently clobbering.

### P2.3 Make accept reversible

**Why:** CONCEPT.md:46 lists "reversible decisions" as a constraint.  Accept
overwrites stable with no snapshot; the agent db is moved to `bin-<id>.db` and
`cleanup_old` unlinks it after seven days.  There is no undo.

**Files:** `src/cairn/orchestrator/orchestrator.py`, both CLIs

Before merging, copy stable's current content for exactly the paths the merge
will touch into the bin workspace:

```python
    async def _snapshot_for_undo(self, ctx: AgentContext, run: RunRecord | None) -> None:
        """Save stable's pre-merge content for the paths this accept will touch."""
        if run is None or self.bin is None:
            return
        prefix = f"undo/{ctx.agent_id}/"
        restored: list[str] = []
        removed: list[str] = []
        for rel in sorted(set(run.written) | set(run.deleted)):
            try:
                content = await self.stable.files.read(rel, mode="binary")
            except Exception:
                removed.append(rel)      # did not exist before: undo = delete
                continue
            await self.bin.files.write(prefix + rel, content, mode="binary")
            restored.append(rel)

        repo = self.bin.kv.repository(prefix="", model_type=UndoRecord)
        await repo.save(f"undo:{ctx.agent_id}", UndoRecord(
            agent_id=ctx.agent_id,
            restore_paths=restored,
            delete_paths=removed,
            created_at=time.time(),
        ))
```

And the inverse operation:

```python
    async def undo_accept(self, agent_id: str) -> dict[str, int]:
        """Restore stable to its pre-accept state for one agent's changes."""
        repo = self.bin.kv.repository(prefix="", model_type=UndoRecord)
        undo = await repo.load(f"undo:{agent_id}")
        if undo is None:
            raise AgentNotFoundError(
                f"No undo record for {agent_id} (already expired or never accepted)",
                error_code="UNDO_NOT_FOUND",
            )
        prefix = f"undo/{agent_id}/"
        for rel in undo.restore_paths:
            content = await self.bin.files.read(prefix + rel, mode="binary")
            await self.stable.files.write(rel, content, mode="binary")
        for rel in undo.delete_paths:
            with suppress(Exception):
                await self.stable.files.remove(rel)
        await repo.delete(f"undo:{agent_id}")
        return {"restored": len(undo.restore_paths), "deleted": len(undo.delete_paths)}
```

Expose `cairn undo <agent-id>`, and have `cleanup_old` remove undo records on
the same schedule it removes lifecycle records.

**Verify:** accept an agent, assert stable changed, `undo_accept`, assert
stable is byte-identical to its pre-accept state.

**Done when:** `cairn accept` followed by `cairn undo` is a no-op on stable.

### P2.4 Guard the gate-bypassing CLI commands

**Why:** `cairn-cli files write stable <path> <content>` writes straight into
stable (`typer_cli.py:377-382`) and `workspace delete stable` unlinks it,
contradicting CONCEPT.md:45 ("Stable state is never mutated without explicit
human acceptance").  Both are one tab-completion away from the review commands.

**Files:** `src/cairn/cli/typer_cli.py`

```python
PROTECTED_WORKSPACES = {"stable", "bin"}


def _guard_direct_write(workspace: str, cairn_home: Path, force: bool) -> None:
    if read_daemon_pid(cairn_home) is not None:
        raise typer.BadParameter(
            "A Cairn daemon is running and owns these databases. "
            "Stop it before writing directly."
        )
    if workspace in PROTECTED_WORKSPACES and not force:
        raise typer.BadParameter(
            f"'{workspace}' is a managed workspace - changes belong in an agent "
            f"overlay reviewed with 'cairn accept'. Pass --force-unsafe to override."
        )
```

Call it from `files_write` and `workspace_delete`, with a
`--force-unsafe` flag.

**Verify:** `test_files_write_stable_refused` and
`test_files_write_refused_while_daemon_running`.

**Done when:** writing to stable requires an explicit unsafe flag and is
impossible while a daemon runs.

---

## Phase 3 — Close the containment gaps

### P3.1 Detach the sandbox from the user's terminal

**Why:** `asyncio.create_subprocess_exec` pipes stdout/stderr but leaves stdin
at the default, which means *inherited* (`sandbox.py:291-295`), and the argv
has no `--new-session` (`sandbox.py:204-248`).  Verified over a pty: agent code
inside the sandbox sees `sys.stdin.isatty() == True`, `fd 0 -> /dev/pts/N`, and
can open `/dev/tty`.  It can consume input meant for your shell and write
escape sequences to your terminal.  (Classic TIOCSTI injection is separately
mitigated by `dev.tty.legacy_tiocsti=0` on modern kernels, but do not rely on
that.)

**Files:** `src/cairn/runtime/sandbox/sandbox.py`

Two lines.  In `_build_argv`, immediately after `--die-with-parent`:

```python
            "--die-with-parent",
            "--new-session",       # detach from the controlling terminal
```

In `_spawn`:

```python
            return await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,   # never inherit the user's tty
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
```

**Verify:** add to the argv unit tests that `--new-session` is present, and add
the pty integration test in P3.4.

**Done when:** the pty test shows `isatty: False` and `/dev/tty` unopenable.

### P3.2 Bound disk, file count and process count

**Why:** `boot.py:_apply_resource_limits` sets `RLIMIT_DATA`/`RLIMIT_AS`,
`RLIMIT_CPU` and the recursion limit — but not `RLIMIT_FSIZE`, `RLIMIT_NOFILE`
or `RLIMIT_NPROC`.  Measured inside a replica sandbox: `FSIZE = unlimited`,
`NPROC = 255736`, `NOFILE = 524288`, and `subprocess.run` succeeds.  `/workspace`
is a bind to real host disk, so a runaway write loop fills your filesystem,
bounded only by the 60-second wall clock.  Forking also multiplies the CPU and
memory budget, since `RLIMIT_CPU` is per-process.

**Files:** `src/cairn/runtime/settings.py`, `src/cairn/runtime/sandbox/sandbox.py`,
`src/cairn/runtime/sandbox/boot.py`

Settings:

```python
class ExecutorSettings(BaseSettings):
    ...
    max_output_file_bytes: int = Field(
        default=64 * 1024 * 1024,
        description="RLIMIT_FSIZE: largest single file the sandbox may create",
    )
    max_processes: int = Field(
        default=64, description="RLIMIT_NPROC: process/thread cap inside the sandbox"
    )
    max_open_files: int = Field(
        default=1024, description="RLIMIT_NOFILE: open file descriptor cap"
    )
    max_workspace_bytes: int = Field(
        default=512 * 1024 * 1024,
        description="Post-run cap on total materialized workspace size",
    )
```

Pass them through as env vars in `_build_argv` alongside the existing ones:

```python
            "--setenv", "CAIRN_MAX_OUTPUT_FILE_BYTES", str(self.settings.max_output_file_bytes),
            "--setenv", "CAIRN_MAX_PROCESSES", str(self.settings.max_processes),
            "--setenv", "CAIRN_MAX_OPEN_FILES", str(self.settings.max_open_files),
```

Apply them in `boot.py`:

```python
MAX_OUTPUT_FILE_BYTES = int(os.environ.get("CAIRN_MAX_OUTPUT_FILE_BYTES", "0") or 0)
MAX_PROCESSES = int(os.environ.get("CAIRN_MAX_PROCESSES", "0") or 0)
MAX_OPEN_FILES = int(os.environ.get("CAIRN_MAX_OPEN_FILES", "0") or 0)


def _set_limit(which: int, value: int) -> None:
    """Best-effort rlimit; never lets the limit rise above the inherited hard cap."""
    if value <= 0:
        return
    try:
        soft, hard = resource.getrlimit(which)
    except (ValueError, OSError):
        return
    capped = value if hard == resource.RLIM_INFINITY else min(value, hard)
    try:
        resource.setrlimit(which, (capped, capped))
    except (ValueError, OSError):
        pass


def _apply_resource_limits() -> None:
    ...existing memory/CPU handling...
    _set_limit(resource.RLIMIT_FSIZE, MAX_OUTPUT_FILE_BYTES)
    _set_limit(resource.RLIMIT_NPROC, MAX_PROCESSES)
    _set_limit(resource.RLIMIT_NOFILE, MAX_OPEN_FILES)
    sys.setrecursionlimit(MAX_RECURSION_DEPTH)
```

`RLIMIT_FSIZE` caps single files; total workspace growth needs a host-side
check.  `_snapshot` already walks the tree, so accumulate as you go and enforce
after the run, before re-import:

```python
        written, deleted = self._diff_snapshot(workdir, baseline)
        total = sum(len(content) for _, content in written)
        if total > self.settings.max_workspace_bytes:
            raise ResourceLimitError(
                f"Sandbox wrote {total} bytes, exceeding the "
                f"{self.settings.max_workspace_bytes} byte workspace budget",
                error_code="WORKSPACE_BUDGET_EXCEEDED",
                context={"agent_id": self.agent_id, "bytes_written": total},
            )
```

Finally, pass `max_content_bytes` when the orchestrator opens workspaces — the
option exists in fsdantic and in Cairn's own `open_workspace` wrapper, and the
orchestrator uses neither (see P4.7).

**Verify:** integration tests that a task writing a 200 MB file dies with
`EFBIG`, and that a task forking 200 processes hits `BlockingIOError` rather
than the machine.

**Done when:** the runaway-write and fork-bomb tests fail fast instead of
being bounded only by wall clock.

### P3.3 Correct the documentation's trust claims

**Why:** `boot.py` `exec()`s task code with the full stdlib available —
`open()`, `os`, `subprocess`, `socket` all work, and `_resolve()`'s traversal
checks only apply to code that voluntarily calls the helpers.  bwrap is the
boundary; the helpers are ergonomics.  But CONCEPT.md:31 says "no direct system
access; all operations go through external functions", SPEC.md:56 calls the
helpers "the canonical capability surface", and SPEC.md:172 says "no host
filesystem" when in fallback mode `/usr`, `/bin`, `/lib` and `/nix/store` are
readable.

**Files:** `docs/CONCEPT.md`, `docs/SPEC.md`

Replace CONCEPT.md principle 2 with something true:

> **2. Isolation over implicit trust**
> Code executes inside a bubblewrap sandbox with no network, an unprivileged
> uid, and only the materialized workspace writable.  Bubblewrap is the
> security boundary — task code is ordinary Python with the full standard
> library, and the `read_file`/`write_file` helpers are ergonomics, not a
> sandbox.  Anything that must not be reachable has to be excluded at the mount
> layer.

And in SPEC.md's sandbox policy section, replace "no host filesystem" with the
accurate statement that the host filesystem is *unwritable*, and that in
fallback mode a read-only view of the system runtime directories is exposed.

**Done when:** a reader cannot come away believing the helper API is a
security boundary.

### P3.4 Add adversarial sandbox tests

**Why:** there is exactly one negative test — that symlinks aren't re-imported
(`test_sandbox_executor.py:54-62`).  Nothing asserts network is blocked, that
writes outside `/workspace` fail, or that host `$HOME` is unreadable.  If
someone drops `--unshare-all` while debugging, all 191 tests still pass.  The
argv *is* the security model, so it needs tests that fail loudly when it
changes.

**Files:** new `tests/cairn/integration/test_sandbox_boundary.py`

```python
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not BWRAP or not SANDBOX_PYTHON, reason="needs bwrap"),
]

BOUNDARY_CASES = [
    ("network", """
        import socket
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=3)
            result = "REACHABLE"
        except OSError:
            result = "blocked"
        write_file("out.txt", result)
    """, "blocked"),

    ("host_home", """
        import os
        write_file("out.txt", "readable" if os.path.isdir(os.path.expanduser("~/.ssh")) else "blocked")
    """, "blocked"),

    ("escape_write", """
        try:
            open("/etc/cairn-escape", "w").write("x")
            result = "WROTE"
        except OSError:
            result = "blocked"
        write_file("out.txt", result)
    """, "blocked"),

    ("tty", """
        import sys
        write_file("out.txt", "TTY" if sys.stdin.isatty() else "blocked")
    """, "blocked"),

    ("fsize", """
        try:
            with open("big.bin", "wb") as fh:
                for _ in range(4096):
                    fh.write(b"x" * (1024 * 1024))
            result = "UNBOUNDED"
        except OSError:
            result = "blocked"
        write_file("out.txt", result)
    """, "blocked"),
]


@pytest.mark.parametrize("name,code,expected", BOUNDARY_CASES, ids=[c[0] for c in BOUNDARY_CASES])
async def test_sandbox_boundary(tmp_path, name, code, expected):
    result = await _run_in_sandbox(tmp_path, textwrap.dedent(code))
    assert result == expected, f"sandbox boundary '{name}' regressed"
```

The `tty` case needs the executor driven under a pty (`pty.openpty()`, pass the
slave fd as the parent's stdin) to be meaningful — otherwise it passes
trivially.  Write that one as a dedicated test.

Also add an argv snapshot test so flag removals are caught without needing
bwrap at all:

```python
REQUIRED_FLAGS = {"--unshare-all", "--die-with-parent", "--new-session", "--clearenv"}


def test_argv_contains_required_isolation_flags():
    argv = _executor()._build_argv()
    missing = REQUIRED_FLAGS - set(argv)
    assert not missing, f"sandbox isolation flags removed: {missing}"
```

**Done when:** deleting `--unshare-all` from `_build_argv` turns the suite red.

---

## Phase 4 — Robustness

### P4.1 Resolve agents stranded by a crash

**Why:** `recover_from_lifecycle_store` re-queues only `QUEUED` records
(`orchestrator.py:173-174`).  An agent that was `GENERATING`/`EXECUTING`/
`SUBMITTING` when the daemon died is restored in that state and never advances:
not re-queued, not acceptable (needs `REVIEWING`), not rejectable (needs
`REVIEWING`/`QUEUED`).  `test_crash_recovery.py:60-83` asserts the stuck state
as correct.

**Files:** `src/cairn/orchestrator/orchestrator.py`, `tests/cairn/integration/test_crash_recovery.py`

```python
INTERRUPTED_STATES = {AgentState.GENERATING, AgentState.EXECUTING, AgentState.SUBMITTING}

    async def recover_from_lifecycle_store(self) -> None:
        ...
            self.active_agents[agent_id] = ctx

            if ctx.state is AgentState.QUEUED:
                await self.queue.enqueue(agent_id, ctx.priority)
            elif ctx.state in INTERRUPTED_STATES:
                # The process died mid-run.  The sandbox workdir and any partial
                # re-import cannot be trusted, so fail the agent explicitly
                # rather than leaving a record nothing can ever resolve.
                ctx.error = format_agent_error(
                    "Interrupted by orchestrator restart",
                    agent_id=agent_id,
                    state=ctx.state.value,
                    task=record.task,
                )
                ctx.transition(AgentState.ERRORED)
                await self._save_lifecycle_record(ctx)
                if self.config.requeue_interrupted:
                    await self.spawn_agent(record.task, TaskPriority(record.priority))
```

Add `requeue_interrupted: bool = False` to `OrchestratorSettings` — opt-in,
because silently re-running a task that may have had side effects is its own
hazard.

Update `test_orchestrator_recovers_in_progress_state` to assert `ERRORED` with
a populated `error`, and add a test that the recovered agent can then be
rejected.

**Done when:** killing the daemon mid-run leaves an agent you can inspect and
clean up, not a permanent zombie.

### P4.2 Stop the worker loop from dying silently

**Why:** `_worker_loop` (`orchestrator.py:447-454`) has no exception handling.
Any exception ends the loop, and because `_worker_task` is created and never
awaited, it is swallowed until GC.  The orchestrator then accepts work forever
without running any of it.  Per-agent tasks have the same problem —
`add_done_callback(self._running_tasks.discard)` discards without inspecting.

**Files:** `src/cairn/orchestrator/orchestrator.py`

```python
    async def _worker_loop(self) -> None:
        while True:
            try:
                queued = await self.queue.dequeue_wait()
                agent_id = queued.task
                await self._semaphore.acquire()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop iteration failed; continuing")
                await asyncio.sleep(WORKER_ERROR_BACKOFF_SECONDS)
                continue

            task = asyncio.create_task(self._run_agent(agent_id))
            self._running_tasks.add(task)
            task.add_done_callback(self._on_agent_task_done)

    def _on_agent_task_done(self, task: asyncio.Task[None]) -> None:
        self._running_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Agent task raised out of _run_agent", exc_info=exc)
```

Add a supervisor so a genuinely dead loop is restarted rather than leaving the
daemon a zombie:

```python
    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            self._worker_task.add_done_callback(self._on_worker_done)

    def _on_worker_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        logger.error("Worker loop exited unexpectedly; restarting", exc_info=task.exception())
        self._ensure_worker()
```

**Verify:** monkeypatch `queue.dequeue_wait` to raise once, assert the loop
survives and still processes the next agent.

**Done when:** an exception in the scheduling path is logged and recovered
from, not silently fatal.

### P4.3 Stop deleting the evidence when a run fails

**Why:** on nonzero exit, `run()` writes `run.log` then raises
(`sandbox.py:153-164`), and `_handle_agent_error` `rmtree`s the whole workdir
(`orchestrator.py:582-584`).  On timeout, `run.log` is never written at all.
Only `stderr[-4000:]` survives, so everything the agent printed via `log()` is
gone — in a workflow whose premise is a human inspecting what an agent did.

**Files:** `src/cairn/runtime/sandbox/sandbox.py`, `src/cairn/orchestrator/orchestrator.py`

In the executor, write the log on the timeout path too:

```python
        except TimeoutError:
            proc.kill()
            stdout, stderr = b"", b""
            with _suppress_timeout():
                stdout, stderr = await proc.communicate()
            partial = f"{stdout.decode('utf-8', 'replace')}\n{stderr.decode('utf-8', 'replace')}".strip()
            (cairn_dir / "run.log").write_text(
                partial + f"\n\n[cairn] killed after {self.settings.max_execution_time}s\n",
                encoding="utf-8",
            )
            raise CairnTimeoutError(...)
```

In the orchestrator, keep the workdir and persist what you have:

```python
    async def _handle_agent_error(self, ctx: AgentContext | None, exc: Exception) -> None:
        if ctx is None:
            return
        ctx.error = str(exc)
        ctx.transition(AgentState.ERRORED)

        # Keep the workdir: run.log and the partial changeset are the only
        # record of what the agent did before it failed.
        workdir = self.cairn_home / "workspaces" / ctx.agent_id
        log_path = workdir / SANDBOX_DIR_NAME / "run.log"
        if log_path.exists():
            with suppress(Exception):
                await self._record_partial_run(ctx, log_path.read_text(encoding="utf-8"))

        await self._save_lifecycle_record(ctx)
```

Workdirs for errored agents are then cleaned by `cleanup_old` on the normal
schedule, or immediately by `cairn reject`.  Add a
`cairn logs <agent-id>` command that prints the run record's log.

**Verify:** run a task that prints then raises; assert
`cairn logs <id>` shows the printed output and that the workdir still exists.

**Done when:** you can debug a failed agent run without re-running it.

### P4.4 Get blocking I/O out of the event loop

**Why:** `_snapshot` sha256s every file and `_diff_snapshot` reads changed
files whole, both synchronously inside `async def run` (`sandbox.py:324-367`) —
stalling every other agent and both watchers for the duration.  Same for
`path.read_bytes()` in the watcher and `persist_state`'s `write_text`.  The only
`asyncio.to_thread` in the codebase is in the unused `regex_utils`.

**Files:** `src/cairn/runtime/sandbox/sandbox.py`, `src/cairn/watcher/watcher.py`,
`src/cairn/orchestrator/orchestrator.py`

The snapshot functions are already `@classmethod`/`@staticmethod` with no
async dependencies, so this is mechanical:

```python
        baseline = await asyncio.to_thread(self._snapshot, workdir)
        ...
        written, deleted = await asyncio.to_thread(self._diff_snapshot, workdir, baseline)
```

Same for the watcher's per-change read:

```python
    async def handle_change(self, change_type: Change, path: Path) -> None:
        ...
        content = await asyncio.to_thread(path.read_bytes)
        await self.workspace.files.write(rel_path, content, mode="binary")
```

For `persist_state`: it is called on every transition and nothing reads the
file (see P5.1).  Either delete it, or make it atomic and throttled:

```python
    async def persist_state(self) -> None:
        now = time.monotonic()
        if now - self._last_persist < STATE_PERSIST_MIN_INTERVAL_SECONDS:
            return
        self._last_persist = now
        payload = {...}
        await asyncio.to_thread(self._write_state_atomic, payload)

    def _write_state_atomic(self, payload: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_file)
```

**Verify:** a benchmark asserting that a long `_snapshot` does not delay a
concurrently scheduled `asyncio.sleep(0.01)` by more than a small margin.

**Done when:** no synchronous file I/O remains on the orchestrator's hot path.

### P4.5 Pin in-use workspaces in the cache

**Why:** `WorkspaceCache._evict_if_needed` (`workspace_cache.py:86-89`) closes
the LRU entry without checking whether a running agent holds the handle.
`_execute_code` holds `agent_fs` across the entire sandbox run and re-import.
With >100 recovered agents this closes a database mid-run.

**Files:** `src/cairn/runtime/workspace_cache.py`, `src/cairn/orchestrator/orchestrator.py`

```python
class WorkspaceCache:
    def __init__(self, max_size: int = MAX_WORKSPACE_CACHE_SIZE) -> None:
        ...
        self._pinned: set[str] = set()

    @asynccontextmanager
    async def pinned(self, key: str) -> AsyncIterator[None]:
        """Protect a cached workspace from eviction while it is in use."""
        async with self._lock:
            self._pinned.add(key)
        try:
            yield
        finally:
            async with self._lock:
                self._pinned.discard(key)
                await self._evict_if_needed()

    async def _evict_if_needed(self) -> None:
        if self.max_size <= 0:
            return
        for key in list(self._cache):
            if len(self._cache) <= self.max_size:
                return
            if key in self._pinned:
                continue
            workspace = self._cache.pop(key)
            await self._close_workspace(workspace, key=key)
        if len(self._cache) > self.max_size:
            logger.warning(
                "Workspace cache over capacity; all entries pinned",
                extra={"size": len(self._cache), "max_size": self.max_size},
            )
```

Wrap the lifecycle in the orchestrator:

```python
    async def _run_agent(self, agent_id: str) -> None:
        ctx = self.active_agents.get(agent_id)
        ...
        async with self.workspace_cache.pinned(str(ctx.agent_db_path)):
            await self._execute_agent_lifecycle(ctx)
```

**Verify:** set `workspace_cache_size=1`, pin one workspace, put two more,
assert the pinned one is still open.

**Done when:** an agent's workspace cannot be closed underneath it.

### P4.6 Broaden `spawn_agent` rollback

**Why:** it rolls back only on `ResourceLimitError` (`orchestrator.py:322-327`).
Any other failure leaks the database file, the cache entry and a ghost
`active_agents` record.

```python
        try:
            await self._save_lifecycle_record(ctx)
            await self.queue.enqueue(agent_id, priority)
        except BaseException:
            self.active_agents.pop(agent_id, None)
            await self.workspace_cache.remove(str(agent_db))
            if self.lifecycle is not None:
                with suppress(Exception):
                    await self.lifecycle.delete(agent_id)
            with suppress(OSError):
                agent_db.unlink(missing_ok=True)
            raise
```

`BaseException` matters here: cancellation during CLI teardown is exactly the
case that leaves ghosts today.

**Done when:** a forced failure in `_save_lifecycle_record` leaves no
`agent-*.db`, no cache entry and no `active_agents` entry.

### P4.7 Shut down cleanly

**Why:** `shutdown()` exists, is correct, and is called by no CLI path.  There
is no SIGINT/SIGTERM handling, so Ctrl-C on the daemon abandons in-flight
agents.  The orchestrator also calls `Fsdantic.open` directly
(`orchestrator.py:105-106`) instead of its own `open_workspace` wrapper, so it
gets no `WorkspaceError` translation and no `max_content_bytes` cap.

**Files:** `src/cairn/orchestrator/orchestrator.py`, `src/cairn/cli/cli.py`

Route the orchestrator's own workspaces through the wrapper:

```python
        self.stable = await self.workspace_manager.create_workspace(
            self.agentfs_dir / "stable.db",
            max_content_bytes=self.config.max_content_bytes,
        )
        self.bin = await self.workspace_manager.create_workspace(
            self.agentfs_dir / "bin.db",
            max_content_bytes=self.config.max_content_bytes,
        )
```

`create_workspace` already tracks them, so drop the explicit
`track_workspace` calls.

Install signal handlers in `_run_up`:

```python
async def _run_up(args) -> int:
    ...
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await orchestrator.initialize()
    runner = asyncio.create_task(orchestrator.run())
    try:
        await asyncio.wait([runner, asyncio.create_task(stop.wait())],
                           return_when=asyncio.FIRST_COMPLETED)
    finally:
        runner.cancel()
        with suppress(asyncio.CancelledError):
            await runner
        await orchestrator.shutdown()
    return 0
```

**Verify:** send SIGTERM to a daemon with an agent mid-run; assert workspaces
are closed and the pidfile is removed.

**Done when:** Ctrl-C drains in-flight agents and exits 0.

### P4.8 Miscellaneous correctness

Small, independent fixes:

- **`reject_agent` message** (`orchestrator.py:389`) says "not in reviewing
  state" while accepting `QUEUED` too.  Fix the message, and remove the agent
  id from the queue heap so the worker doesn't dequeue a phantom.  Add
  `TaskQueue.remove(task_id) -> bool`.
- **File mode is lost.**  `_snapshot` tracks content only, so a `chmod +x`
  inside the sandbox does not survive re-import.  Record
  `(digest, st_mode & 0o777)` in the manifest and store the mode in the run
  record; apply it on materialize.  If fsdantic has no mode support, store the
  executable set in `RunRecord` and re-apply after `to_disk`.
- **Empty directories are lost** — same manifest, add a directory set.
- **`AgentContext.transition` enforces nothing** (`agent.py:60-63`) despite
  `AgentStateError` existing and SPEC.md:227 declaring a state machine:

```python
VALID_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.QUEUED:     frozenset({AgentState.GENERATING, AgentState.REJECTED, AgentState.ERRORED}),
    AgentState.GENERATING: frozenset({AgentState.EXECUTING, AgentState.ERRORED}),
    AgentState.EXECUTING:  frozenset({AgentState.SUBMITTING, AgentState.ERRORED}),
    AgentState.SUBMITTING: frozenset({AgentState.REVIEWING, AgentState.ERRORED}),
    AgentState.REVIEWING:  frozenset({AgentState.ACCEPTED, AgentState.REJECTED, AgentState.ERRORED}),
    AgentState.ACCEPTED:   frozenset(),
    AgentState.REJECTED:   frozenset(),
    AgentState.ERRORED:    frozenset({AgentState.REJECTED}),
}


    def transition(self, new_state: AgentState) -> None:
        if new_state not in VALID_TRANSITIONS[self.state]:
            raise AgentStateError(
                f"Invalid transition {self.state.value} -> {new_state.value}",
                error_code="INVALID_STATE_TRANSITION",
                context={"agent_id": self.agent_id, "from": self.state.value, "to": new_state.value},
            )
        self.state = new_state
        self.state_changed_at = time.time()
```

  Expect this to surface latent bugs in the recovery path — that is the point.
  Land it *after* P4.1, which adds the `EXECUTING -> ERRORED` edge it needs.

- **`_run_agent`'s dead branch** (`orchestrator.py:467-470`): the
  `except CairnError` / `isinstance(exc, RecoverableError)` check produces the
  same outcome either way.  Collapse it into the general handler.
- **`cleanup_old`** (`lifecycle.py:211-235`) unlinks `record.db_path` without
  checking the workspace cache, and never removes
  `$CAIRN_HOME/workspaces/{agent_id}`.  Remove from the cache first, then unlink
  both.

---

## Phase 5 — Hygiene

### P5.1 Delete dead code

Verified unused by anything in `src/`:

| Target | Note |
| --- | --- |
| `src/cairn/utils/regex_utils.py` | referenced only by its own test |
| `runtime/resource_limits.run_with_timeout` | no callers |
| `core/types.Result` | no callers |
| `AgentContext.execution_result` | set to a constant, never read |
| `persist_state` / `orchestrator.json` | nothing in `src/`, `tests/` or `docs/` reads it |
| Most of `core/constants.py` | `SIGNAL_POLL_INTERVAL_SECONDS`, `WATCHER_DEBOUNCE_SECONDS`, `MAX_FILE_SIZE_BYTES`, `DEFAULT_MAX_MEMORY_BYTES`, `DEFAULT_QUEUE_PRIORITY`, `MIN/MAX_QUEUE_PRIORITY`, `DEFAULT_MAX_CONCURRENT_AGENTS`, `AGENT_ID_*`, `DEFAULT_RETRY_*` |

Delete `regex_utils.py` and its test together.  Some constants get reused by
earlier phases (`SIGNAL_POLL_INTERVAL_SECONDS` → `SIGNAL_SWEEP_INTERVAL_SECONDS`
in P1.5, `WATCHER_DEBOUNCE_SECONDS` if you add debouncing) — do this step last
so you are not deleting something a later phase wants.

Confirm before each deletion:

```bash
rg -n "run_with_timeout|regex_utils|Result\.ok|execution_result" src/ tests/ extensions/
```

### P5.2 Fix or remove `RetryStrategy.with_retry_sync`

It calls `asyncio.get_event_loop().run_until_complete()` from inside a running
loop (`utils/retry.py:126`), which always raises.  It has no callers.  Delete
it, or make it genuinely synchronous with `time.sleep`.

### P5.3 Collapse the two retry modules

`utils/retry.py` (`RetryStrategy`) and `utils/retry_utils.py` (`with_retry`)
are two layers over one concern, and `with_retry` builds a fresh
`RetryStrategy` on every call.  Merge into `utils/retry.py`, keep `with_retry`
as the public decorator, re-export from `utils/retry_utils.py` for one release
with a `DeprecationWarning`.

### P5.4 Single-source the version

`src/cairn/__init__.py:57` says `0.2.1`; `pyproject.toml:3` says `0.3.0`.

```python
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cairn")
except PackageNotFoundError:      # editable/source checkout
    __version__ = "0.0.0.dev0"
```

Add `test_version_matches_metadata`.

### P5.5 Fix or delete the demo script

`scripts/demo_cairn_library.py` loads `src/cairn/queue.py` and
`src/cairn/retry.py`, which moved during the package reorg.  It dies with
`FileNotFoundError`.  Either update the paths to
`src/cairn/orchestrator/queue.py` and `src/cairn/utils/retry.py`, or delete it
— the test suite covers what it demonstrates.  If you keep it, add it to CI so
it cannot rot again:

```yaml
      - name: Smoke-test the demo script
        run: uv run python scripts/demo_cairn_library.py
```

### P5.6 Clean up `AgentStateManager`

**Files:** `src/cairn/runtime/state.py`

Two problems.  Line 150 reaches through `self._kv._agent_fs.kv.list(...)`, but
a public `agent_fs` property exists (fsdantic `kv.py:273`) *and* `KVManager.list`
already handles prefix qualification — the current call bypasses it and would
list the wrong namespace if the manager had a base prefix.  And the bare
`except Exception: return []` means that if this ever drifts, `clear_all`
becomes a silent no-op that still reports success.

```python
    async def list_keys(self) -> list[str]:
        """List all state keys for this agent (without the agent prefix)."""
        entries = await self._kv.list(prefix=self._prefix)
        return [entry["key"][len(self._prefix):] for entry in entries]
```

Apply the same treatment to `get`, `exists` and `get_last_active`: catch the
specific fsdantic exceptions you expect, and let everything else propagate.  A
state store that silently reports "no data" on a real error is worse than one
that raises.

### P5.7 Repository tidying

- Delete `CAIRN_LIBRARY_REVIEW.md` and `CAIRN_CONCURRENCY_RECOMMENDATIONS.md`
  from the repo root — both are stale (they describe "Grail" and AgentFS 0.4,
  neither of which matches the current architecture) and contradict
  `docs/SPEC.md`, which is declared the source of truth.  Move anything still
  true into `docs/`.
- Untrack the 4 MB vendored `.context/` (add to `.gitignore`); it duplicates
  dependencies already pinned in `uv.lock`.  Keep a local copy if you find it
  useful for reading.
- Update `docs/SPEC.md` for every contract this plan changes — the source-of-truth
  note at the top of the file requires it in the same PR.  At minimum: the
  signal transport now has a producer (P1.4), the CLI is a thin client (P1.4),
  `FileWatcher` performs an initial sync and appears in the architecture section
  (P1.2), accept can be refused or undone (P2.2/P2.3), and the sandbox policy
  wording (P3.3).

### P5.8 Decide the fate of the second CLI

`cairn` (argparse) and `cairn-cli` (Typer) have overlapping commands and
diverging behavior — only one closes its workspaces, only one has tests,
neither calls `shutdown()`.  Maintaining both means every fix in this plan
lands twice.

Recommendation: keep the Typer app (richer output, already has a test file),
port the daemon commands (`up`, `queue`, `spawn`, `accept`, `reject`, `status`,
`list-agents`, plus the new `run`, `undo`, `logs`) into it, and make
`cairn` an alias for the same entry point.  Do this *after* Phase 1, so you
port the fixed implementation rather than porting the bug and fixing it twice.

---

## Appendix A — Three decisions to make before starting

These shape the work and are yours to make; the plan above assumes a default
for each.

**1. Single-writer or multi-writer?**  The plan assumes **single-writer**: the
daemon owns the databases, the CLI writes signals and reads read-only (P1.4).
The alternative is to make multi-process writes genuinely safe with
`enable_mvcc=True` and cross-process locking — but `workspace_manager.py`'s own
module docstring records that pyturso 0.7.2 does not reliably surface
write-write conflicts, so that path means building the coordination yourself.
Single-writer is much less work and matches how the tool is actually used.

**2. Should accept be refusable by default?**  The plan says yes (P2.2): a
stale base refuses and tells you to use `--force`.  If you would rather not
interrupt flow, invert it — accept proceeds but prints a prominent warning and
records the overwritten paths in the undo journal.  Given the goal is
preventing the common failure, refusing by default is the safer default; the
undo journal (P2.3) is what makes it recoverable either way.

**3. How much should a failed run keep?**  P4.3 keeps the whole workdir, which
for a large project is a full materialized tree per failed agent.  If that is
too much disk, keep only `.cairn/` (task, log, submission) and delete the
materialized files.  You lose the ability to inspect partial file edits, but
you keep the debugging surface that matters most.

---

## Suggested landing order

Each of these is a coherent PR:

| PR | Steps | Why this grouping |
| --- | --- | --- |
| 1 | P0.1–P0.3 | Get a green baseline |
| 2 | P1.1, P1.2 | Agents can finally see the project |
| 3 | P1.3, P1.4, P1.5, P1.6 | The CLI/daemon rearchitecture, one change |
| 4 | P2.1 | Review shows ground truth |
| 5 | P2.2, P2.3 | Accept becomes safe and reversible |
| 6 | P3.1, P3.2, P3.4 | Containment plus the tests that hold it |
| 7 | P3.3, P5.7 | Documentation catches up with reality |
| 8 | P4.1, P4.2, P4.6, P4.7 | Failure-mode robustness |
| 9 | P4.3, P4.4, P4.5, P4.8 | Debuggability and performance |
| 10 | P5.1–P5.6, P5.8 | Hygiene, once nothing else depends on it |

Run `ty check` and `devenv test` on every PR.  PRs 3, 5 and 6 change
documented contracts — update `docs/SPEC.md` in the same commit, per the
source-of-truth note.
