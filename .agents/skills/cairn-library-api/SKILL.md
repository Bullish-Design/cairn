---
name: cairn-library-api
description: >-
  Embedding Cairn as a library: open_workspace and WorkspaceManager (WAL/MVCC,
  readonly, busy_timeout, max_content_bytes), WorkspaceInspector and
  WorkspaceStats, AgentStateManager (namespaced KV with typed models and atomic
  counters), TaskQueue, retry utilities, CairnOrchestrator embedding patterns,
  and the public exports of the cairn and cairn.runtime packages. Use when
  building downstream consumers on Cairn's stable APIs.
license: MIT
metadata:
  subsystem: runtime
---

# Cairn Library API

Cairn exposes a stable public API for downstream consumers (e.g. Remora) and
for embedding the runtime in other applications. Everything below is
re-exported from `cairn` and `cairn.runtime`.

```python
from cairn import (
    AgentContext, AgentState, AgentStateManager,
    BwrapExecutor, CairnOrchestrator, CodeProvider,
    ExecutorSettings, FileCodeProvider, FileWatcher, InlineCodeProvider,
    OrchestratorSettings, PathsSettings, QueuedTask, RetryStrategy,
    SandboxExecutionError, SandboxResult, SignalHandler,
    TaskPriority, TaskQueue, WorkspaceInspector, WorkspaceManager,
    WorkspaceStats, open_workspace, resolve_code_provider, with_retry,
)
```

## Workspace opening — `open_workspace`

`cairn.runtime.workspace_manager.open_workspace(path, *, readonly=False,
enable_wal=True, enable_mvcc=False, busy_timeout_ms=5000,
max_content_bytes=None) -> Workspace`

- The **caller owns the returned workspace** and must `await workspace.close()`.
- `readonly=True`: writes raise `WorkspaceError(WORKSPACE_READONLY)`; the
  database file must already exist (`WORKSPACE_NOT_FOUND` otherwise); reads
  never trigger maintenance writes.
- `enable_mvcc=True` implies WAL and allows multiple connections to write
  concurrently.
- `busy_timeout_ms`: milliseconds a write waits on a contended lock (0 =
  fail immediately).
- `max_content_bytes`: caps `files.write`/`write_many` and `kv.set` payloads
  (`WorkspaceError(CONTENT_TOO_LARGE)`); `None` = unbounded.
- fsdantic errors are translated to `cairn.core.exceptions.WorkspaceError`
  preserving the meaningful codes.

```python
from cairn import open_workspace

ws = await open_workspace("path/to.db", readonly=True)
try:
    content = await ws.files.read("/README.md")
finally:
    await ws.close()
```

### Concurrency guarantees (Turso/libSQL)

- **WAL mode is default**: unlimited concurrent readers beside one writer.
  Read operations never block writes and vice versa.
- A single workspace connection serializes its own operations on a worker
  thread — **no `asyncio.Lock` needed for sequential access** to one workspace.
- MVCC (`enable_mvcc=True`): concurrent writers from multiple connections,
  **but** write-write conflicts are *not reliably surfaced* by the driver
  (last-write-wins). For atomic read-modify-write use
  `async with workspace.serialized():` (same-process per-workspace lock) or
  the versioned repository CAS (`VersionedKVRecord` + `KVConflictError`).
- The daemon owns the databases; **inspection tooling must open read-only**
  (pyturso 0.7.2 takes an exclusive lock even for read-only opens, so two
  processes cannot share `bin.db` — that's why the CLI reads the lifecycle
  mirror instead).

## WorkspaceManager

`WorkspaceManager` tracks open workspaces and closes them all on exit:

- `create_workspace(path, **kw) -> Workspace` — opens and tracks; you close via
  `close_workspace(ws)` or `close_all()`.
- `open_workspace(...)` — async context manager (opens, yields, closes).
- `manage_workspace(ws, path=...)` — context manager for a pre-opened workspace.
- `track_workspace` / `untrack_workspace` — manual bookkeeping.
- `close_all()` — best-effort close of every tracked workspace (idempotent).

## WorkspaceInspector

Read-only workspace access for CLIs and diagnostics
(`cairn.runtime.inspection`):

- `WorkspaceInspector(workspace)` — wraps an existing workspace (caller
  retains ownership, inspector never closes it).
- `await WorkspaceInspector.from_path(path)` — opens read-only and **owns**
  the workspace; usable as `async with await WorkspaceInspector.from_path(...)`.
- Methods: `tree(path="/", max_depth=None)`, `list_dir(path="/",
  include_stats=False)` (names, or `{name,size,type}` dicts),
  `read(path)` / `read_bytes(path)`, `exists(path)`, and
  `stats() -> WorkspaceStats(file_count, dir_count, total_bytes)`.

```python
from cairn import WorkspaceInspector

async with await WorkspaceInspector.from_path("stable.db") as inspector:
    names = await inspector.list_dir("/")
    stats = await inspector.stats()
```

## AgentStateManager

Typed state persistence in a workspace's KV store, namespaced per agent
(`cairn.runtime.state`):

```python
from cairn import AgentStateManager

state = AgentStateManager(workspace, "agent-123")  # keys under agent:agent-123:

await state.set("last_file", "/src/main.py")
await state.get("last_file")                 # -> "/src/main.py"
await state.increment("turns")               # atomic counter, starts at 1
turn = await state.increment_turn()          # convenience turn counter
await state.get_turn()
```

- `get(key, default=None)` / `set(key, value)` (JSON-serializable) /
  `delete(key) -> bool` / `exists(key)` / `list_keys()` (prefix-stripped) /
  `clear_all() -> int`.
- `get_typed(key, model)` / `set_typed(key, value)` — Pydantic model
  round-tripping via `model_validate` / `model_dump(mode="json")`.
- `increment(key, amount=1)` uses fsdantic's atomic per-key increment — safe
  for parallel agents sharing a workspace; non-numeric stored values reset to
  0 first.
- `touch()` / `get_last_active()` — activity timestamps.

## TaskQueue & priorities

`cairn.orchestrator.queue`:

```python
from cairn import TaskPriority, TaskQueue

queue = TaskQueue(max_size=1000)
await queue.enqueue("task", TaskPriority.HIGH)   # LOW=1, NORMAL=2, HIGH=3, URGENT=4
task = await queue.dequeue_wait()                # highest priority, then FIFO
```

- `QueuedTask(task, priority)` is a dataclass; heap ordering is
  `(-priority, created_at)`.
- `enqueue` raises `ResourceLimitError(QUEUE_FULL)` when at capacity
  (`max_size=0` disables the cap).
- `dequeue()` returns `None` when empty; `dequeue_wait()` blocks;
  `peek/remove/list_all/clear/size/is_empty/is_full` round out the surface.

## Retry utilities

`cairn.utils.retry`:

- `with_retry(max_attempts=3, initial_delay=1.0, max_delay=60.0,
  backoff_factor=2.0, retry_exceptions=(Exception,), logger=None)` — decorator
  for async functions with exponential backoff.
- `RetryStrategy` — the same engine as a reusable object:
  `await strategy.with_retry(operation, error_handler=..., retry_exceptions=...)`
  plus a synchronous `with_retry_sync`.

## Embedding the orchestrator

```python
from cairn import CairnOrchestrator
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings
from cairn.providers.providers import InlineCodeProvider

orch = CairnOrchestrator(
    project_root=".",
    cairn_home="~/.cairn",                 # optional; defaults to ~/.cairn
    config=OrchestratorSettings(max_concurrent_agents=2),
    executor_settings=ExecutorSettings(),  # sandbox limits, CAIRN_EXECUTOR_* env
    code_provider=InlineCodeProvider(),
    executor_factory=None,                 # override BwrapExecutor for tests/stubs
)
await orch.initialize()                    # opens DBs, initial sync, worker start
try:
    agent_id = await orch.spawn_agent("print('hi')", TaskPriority.HIGH)
    record = await orch.wait_for_agent(agent_id, timeout=60.0)  # terminal state
    # ... review, then:
    stats = await orch.accept_agent(agent_id)   # {"files_merged": n, "tombstones_applied": n}
    # or: await orch.reject_agent(agent_id)
    # or (after accept): await orch.undo_accept(agent_id)
finally:
    await orch.shutdown()
```

Notes for embedders:

- `initialize()` runs the project sync (`sync_project_on_start=True` by
  default) and starts the worker loop unless
  `config.start_worker_on_init=False` (use the latter when you schedule runs
  manually).
- `spawn_agent(task, priority)` returns an `agent-<hex>` id and enqueues the
  task; `wait_for_agent` polls until a terminal state
  (`REVIEWING`/`ACCEPTED`/`REJECTED`/`ERRORED`).
- `submit_command(CairnCommand)` is the normalized dispatch entry point (the
  CLI/signal layer parses into these typed commands first — see
  [cairn-cli-operations](../cairn-cli-operations/SKILL.md)).
- `accept_agent(agent_id, force=False)` refuses with `ACCEPT_STALE_BASE` if
  stable changed on touched paths since the agent read them.
- `cleanup_completed_agents(max_age_seconds=7*DAY)` runs retention (records,
  `bin-{id}.db` files, workdirs, undo snapshots).

## Direct sandbox execution (BwrapExecutor)

For running code over a workspace without the full lifecycle:

```python
from cairn import BwrapExecutor
from cairn.runtime.settings import ExecutorSettings

result = await BwrapExecutor(
    agent_id="x",
    workdir="/tmp/ws",
    agent_fs=agent_workspace,
    stable=stable_workspace,
    settings=ExecutorSettings(),
).run(code=code_text, task="description")
# result: SandboxResult(submission, changes={"written": [...], "deleted": [...]},
#                      log, base_hashes, exit_code, executable, directories)
```

`BwrapExecutor.run` materializes → runs in bwrap → re-imports the changeset
into `agent_fs` and records tombstones for deletions. Raises
`SandboxExecutionError` (launch/nonzero exit) or
`cairn.core.exceptions.TimeoutError` (`EXECUTION_TIMEOUT`).

## Related

- SPEC public API section: [SPEC](../../../docs/SPEC.md).
- API implementations: `src/cairn/runtime/{workspace_manager,inspection,state}.py`,
  `src/cairn/orchestrator/{orchestrator,queue}.py`,
  `src/cairn/utils/retry.py`.
- Test patterns: `tests/cairn/test_workspace_api.py`,
  `tests/cairn/test_state.py`, `tests/cairn/test_fsdantic_features.py`.
