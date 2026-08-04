# Cairn Technical Specification

Version: 1.3
Status: Active
Updated: 2026-03-03

## Canonical scope of this document

`SPEC.md` is the single source of truth for:
- current runtime architecture,
- filesystem/runtime contracts,
- orchestrator state and CLI behavior.

For philosophy and constraints, see [CONCEPT.md](CONCEPT.md). For install/quickstart, see [README.md](../README.md).

## Runtime architecture

Cairn runtime contracts are implemented by four concrete layers:

1. **Code Sourcing (`CodeProvider` protocol)**
   - Code providers implement `get_code(reference, context) -> str` to supply executable Python code.
   - Built-in providers:
     - `FileCodeProvider` - loads code from Python script files on disk
     - `InlineCodeProvider` - treats reference as code itself
   - Plugin providers (separate packages):
     - `LLMCodeProvider` (cairn-llm) - generates code from natural language
     - `GitCodeProvider` (cairn-git) - loads code from git repositories
     - `RegistryCodeProvider` (cairn-registry) - fetches code from registries
   - The orchestrator accepts any `CodeProvider` implementation via constructor parameter.

2. **Storage (`fsdantic.Workspace`)**
   - The orchestrator opens `stable.db`, `bin.db`, and per-agent `agent-*.db` via `Fsdantic.open(path=...)`.
   - Runtime file access and preview materialization use fsdantic workspace manager APIs:
     - file operations (`workspace.files.read/write/query/search`),
     - KV operations (`workspace.kv.get/set/delete/list`),
     - overlay operations (`workspace.overlay.merge/list_changes/reset`),
     - materialization (`workspace.materialize.to_disk/diff`).

3. **Execution (`BwrapExecutor` over a materialized workspace)**
   - Code providers generate or fetch code that is written to `$CAIRN_HOME/workspaces/{agent_id}/.cairn/task.py`.
   - The agent overlay (over stable) is materialized to a real directory via `workspace.materialize.to_disk()`.
   - The code runs as stock CPython inside a bubblewrap sandbox (`BwrapExecutor`): only the materialized
     directory is writable; the interpreter runtime is mounted read-only; network, host filesystem, and
     other processes are unshared.
   - After execution the sandbox changeset is re-imported into the agent overlay (added/changed files
     written, deleted files tombstoned) and `submit_result` payloads are read from `.cairn/submission.json`.
   - Execution limits (wall-clock timeout, memory, CPU, recursion) are enforced via the sandbox bootstrap
     (rlimits) and host-side subprocess timeout.

4. **Sandbox API (`cairn.runtime.sandbox.boot`)**
   - The bootstrap script shipped into the sandbox exposes helper functions
     (`read_file`, `write_file`, `list_dir`, `file_exists`, `search_files`,
     `search_content`, `submit_result`, `log`) as plain functions over the
     workspace directory.  These are **ergonomics, not a security boundary**
     — task code is ordinary Python with the full standard library, so
     anything that must not be reachable must be excluded at the mount layer
     (see the sandbox policy below).
   - `submit_result(...)` writes `.cairn/submission.json`; the host persists it to the agent workspace
     KV submission record consumed by the orchestrator lifecycle flow.

> **Source-of-truth note:** If runtime behavior in code and this section differ, update this section and the implementing modules together in the same change (`src/cairn/orchestrator/orchestrator.py`, `src/cairn/providers/providers.py`, `src/cairn/runtime/sandbox/sandbox.py`).

## Public inspection and state APIs

Cairn exposes public APIs for workspace inspection and agent state management. These are the stable entry points for downstream consumers (e.g. Remora).

### Workspace opening

`open_workspace(path, *, readonly=False) -> Workspace` (`cairn.runtime.workspace_manager`)

Opens a workspace database without requiring a context manager. The caller owns the returned workspace and is responsible for closing it. Wraps internal errors in `WorkspaceError` with error code `WORKSPACE_OPEN_FAILED`.

### Workspace inspection

`WorkspaceInspector` (`cairn.runtime.inspection`) provides read-only workspace access:

- `WorkspaceInspector(workspace)` — wraps an existing workspace (caller retains ownership)
- `await WorkspaceInspector.from_path(path)` — opens a workspace in readonly mode (inspector owns lifecycle)
- Supports `async with` for automatic cleanup when created via `from_path`

**Methods:**
- `tree(path="/", max_depth=None)` — directory tree as nested dicts
- `list_dir(path="/", include_stats=False)` — directory listing (names or name+size+type dicts)
- `read(path)` / `read_bytes(path)` — file contents as text or bytes
- `exists(path)` — path existence check
- `stats()` — returns `WorkspaceStats(file_count, dir_count, total_bytes)`

### Agent state management

`AgentStateManager` (`cairn.runtime.state`) provides typed state persistence via the workspace KV store:

- `AgentStateManager(workspace, agent_id)` — state is automatically namespaced under `agent:{agent_id}:`
- `get(key, default=None)` / `set(key, value)` / `delete(key)` / `exists(key)` — basic KV operations
- `get_typed(key, model)` / `set_typed(key, value)` — Pydantic model serialization
- `increment(key, amount=1)` — atomic counter increment
- `increment_turn()` / `get_turn()` — convenience turn counter
- `touch()` / `get_last_active()` — activity timestamps
- `list_keys()` — all keys for this agent (stripped of prefix)
- `clear_all()` — remove all state for this agent

### Top-level exports

All public APIs are re-exported from `cairn` and `cairn.runtime`:

```python
from cairn import open_workspace, WorkspaceInspector, WorkspaceStats, AgentStateManager
# or
from cairn.runtime import open_workspace, WorkspaceInspector, WorkspaceStats, AgentStateManager
```

## Data layout contract

```text
$PROJECT_ROOT/.agentfs/
├── stable.db
├── agent-{id}.db
└── bin.db

$CAIRN_HOME/ (default ~/.cairn)
├── workspaces/
│   └── {agent_id}/          # Sandbox workdir == preview workspace
│       ├── .cairn/
│       │   ├── task.py          # Generated/loaded agent code
│       │   ├── task.json        # Task inputs (task_description)
│       │   ├── boot.py          # Sandbox bootstrap (shipped in)
│       │   ├── submission.json  # submit_result payload
│       │   └── run.log          # Sandbox stdout/stderr
│       └── ...                  # Materialized workspace files
├── signals/
└── state/
```

4. **Project sync (`FileWatcher`)**
   - `FileWatcher` mirrors the project tree into `stable` once at orchestrator
     startup (`initial_sync`, before the worker loop starts) and then watches
     for filesystem changes, mirroring them into `stable` continuously.
   - Ignoring is name-based (`watchfiles.DefaultFilter` + Cairn's exclusions:
     `.agentfs`, `.jj`, `.devenv`, `.direnv`, `venv`, `.ruff_cache`,
     `.coverage`, `htmlcov`, `dist`, `build`, `target`, `.eggs`, plus db/so
     suffixes).  Files larger than `max_sync_file_bytes` are skipped.

5. **Daemon ownership (P1.4)**
   - The daemon owns all databases.  The CLI never constructs an orchestrator:
     mutations go through signal files, queries read the lifecycle mirror.

## Storage contracts (fsdantic workspaces)

### Overlay semantics

- Reads in an agent overlay must fall through to stable when a path is absent in the overlay.
- Writes in an agent overlay must only update that overlay.
- Accept copies selected overlay changes into stable.
- Reject discards overlay changes.

### Required operations

- `read_file(path) -> bytes`
- `write_file(path, content) -> None`
- `readdir(path) -> list[DirEntry]`
- `stat(path) -> FileStat`
- `remove(path) -> None`
- `mkdir(path) -> None`
- KV store: `get/set/delete/list`

## Execution contracts (bwrap sandbox)

### Task file structure

All executable code is written as a plain Python file (`.cairn/task.py`) with the
following shape. There are no declarations, imports, or restricted dialect —
the sandbox API is injected as globals:

```python
# Inputs are injected as globals from task.json
task_description

# Task code — plain Python, stdlib available
content = read_file("src/main.py")
# ... process ...
write_file("src/main.py", content)

# Submission — must be recorded before the script exits
submit_result(summary="Done", changed_files=["src/main.py"])
```

### Sandbox policy

- The sandbox runs stock CPython inside bubblewrap (`bwrap --unshare-all`).
- Only the materialized workspace directory is writable; the interpreter runtime
  is mounted read-only from a declarative Nix store closure manifest
  (``pkgs.writeClosure`` in ``devenv.nix``; falls back to the immutable
  ``/nix/store`` plus conventional system dirs when no manifest is configured).
- No network, no other processes, no environment variables (``--clearenv``).
  The host filesystem is **unwritable**; in fallback mode (no closure
  manifest) a read-only view of the system runtime directories (``/usr``,
  ``/bin``, ``/lib``, ``/nix/store``) is mounted so the interpreter can run.
- The sandbox is detached from the controlling terminal (``--new-session``,
  stdin is ``/dev/null``): ``sys.stdin.isatty()`` is False and ``/dev/tty``
  cannot be opened.
- Resource limits inside the sandbox: ``RLIMIT_DATA``/``RLIMIT_AS``
  (memory), ``RLIMIT_CPU`` (CPU seconds), ``RLIMIT_FSIZE`` (largest single
  file), ``RLIMIT_NPROC`` (process/thread count), ``RLIMIT_NOFILE`` (open
  descriptors), plus a host-side workspace-size budget enforced after the
  run.
- Bubblewrap is the security boundary: task code is ordinary Python with the
  full standard library.  The sandbox API's path confinement (absolute paths
  and ``..`` traversal rejected) is an ergonomic convenience for code that
  voluntarily uses the helpers — anything that must not be reachable must be
  excluded at the mount layer.
- Symlinks in the workspace are never followed by the host-side re-import.

### Sandbox runtime configuration (NixOS/devenv)

The sandbox runtime is declared in ``devenv.nix`` and consumed via environment
variables (``ExecutorSettings`` uses the ``CAIRN_EXECUTOR_`` prefix):

- ``CAIRN_EXECUTOR_BWRAP_PATH`` — path to the bubblewrap binary.
- ``CAIRN_EXECUTOR_PYTHON_PATH`` — the sandbox interpreter (``pkgs.python3``:
  stdlib only, empty site-packages).
- ``CAIRN_EXECUTOR_SANDBOX_CLOSURE_PATH`` — a file listing the interpreter's
  Nix store closure (built with ``pkgs.writeClosure``), one path per line; the
  executor binds exactly those paths read-only. When unset/missing, the
  executor falls back to binding the immutable ``/nix/store`` plus conventional
  system directories.

### Sandbox API exposed to code (globals, no imports needed)

- `read_file(path) -> str`
- `write_file(path, content) -> bool`
- `list_dir(path='.') -> list[str]`
- `file_exists(path) -> bool`
- `delete_file(path) -> bool`
- `search_files(pattern) -> list[str]`
- `search_content(pattern, path='.') -> list[dict]`
- `submit_result(summary, changed_files) -> bool`
- `log(message) -> bool`

Deletions re-import into the agent overlay as **tombstones** (fsdantic >= 0.7.0
``overlay.tombstone``): the path is removed from the overlay and a
``fsdantic:tombstone:<path>`` KV marker is recorded, so stable-only files can
be deleted too — the accept merge replays the markers against stable
(``MergeResult.tombstones_applied``).  A file re-created in the overlay after
deletion makes its marker inert (the file phase wins).

### Pre-flight validation

Provider-level `validate_code` is the only pre-flight gate (no check-time
declared external validation). Syntax errors surface as sandbox tracebacks that
mark the agent ERRORED.

Validation errors prevent execution and transition agent to ERRORED state.

## Orchestrator contracts

### Agent lifecycle

`QUEUED -> GENERATING -> EXECUTING -> SUBMITTING -> REVIEWING -> (ACCEPTED | REJECTED | ERRORED)`

### Lifecycle metadata storage

Agent lifecycle metadata is stored in a **single canonical location**: the `bin.db` AgentFS KV namespace. This provides:

- Single source of truth for all agent state (active and completed)
- Clear recovery path on orchestrator restart
- Linear, idempotent cleanup operations
- No duplicate writes across multiple storage layers

**KV Schema:**
```
agent:{agent_id} -> {
  agent_id: str,
  task: str,
  priority: int,
  state: str,  # AgentState enum value
  created_at: float,
  state_changed_at: float,
  db_path: str,  # Path to agent-*.db or bin-{agent_id}.db
  submission: dict | null,
  error: str | null
}
```

**Lifecycle operations:**
- All state transitions write to `bin.db` KV store via `LifecycleStore.save()`
- Recovery rebuilds `active_agents` from KV store on startup
- Cleanup is idempotent: `trash_agent()` can be called multiple times safely
- Retention policy removes old completed agents from single location

### Responsibilities

- accept normalized `CairnCommand` ingress and dispatch to command handlers (`queue/accept/reject/status/list_agents`),
- treat CLI and signal files as transport adapters that both parse into the same command model before dispatch,
- optionally monitor signal files (`spawn/queue/accept/reject`) when signal polling is enabled,
- enqueue tasks into a priority queue,
- run a long-lived worker loop that acquires an `asyncio.Semaphore(max_concurrent_agents)` slot before starting each task,
- release the semaphore slot in one completion `finally` path,
- use `CodeProvider` to source executable code (from files, LLMs, git, etc.),
- validate code via provider `validate_code()` before execution,
- write code to `$CAIRN_HOME/workspaces/{agent_id}/.cairn/task.py`,
- execute code via `BwrapExecutor` (materialize → sandbox run → re-import),
- materialize the workspace via `workspace.materialize.to_disk()` (workdir doubles as preview),
- persist lifecycle metadata to canonical KV store on every state transition,
- persist queue stats snapshot under `$CAIRN_HOME/state/` (stats only, not agent metadata).

### CLI contract (thin client, P1.4)

The daemon owns the databases.  The CLI is a thin client: it never constructs
an orchestrator.  Mutating commands write a signal file for the daemon to pick
up; query commands read the daemon's lifecycle mirror (`$CAIRN_HOME/state/lifecycle.json`)
read-only — pyturso 0.7.2 locks database files exclusively even for read-only
opens, so the CLI cannot open `bin.db` while the daemon holds it.

- `cairn up` - Start the daemon (claims `$CAIRN_HOME/state/orchestrator.pid`; a
  second `cairn up` in the same `CAIRN_HOME` is refused)
- `cairn run <task> [--timeout N]` - Run a single task inline to completion
  (no daemon; refused while a daemon is running)
- `cairn spawn <reference>` - High-priority task; writes a `spawn` signal
- `cairn queue <reference>` - Normal-priority task; writes a `queue` signal
- `cairn list-agents` - Read the lifecycle mirror
- `cairn status <agent-id>` - Read the lifecycle mirror; exit 1 + friendly
  message for unknown agents (no traceback)
- `cairn accept <agent-id> [--timeout N] [--force]` - Write an `accept` signal, then poll
  the mirror until the accept settles.  Without `--force`, accept is refused
  (`ACCEPT_STALE_BASE`) if stable changed for any path the agent touched since
  the agent read it.
- `cairn reject <agent-id> [--timeout N]` - Write a `reject` signal, then poll
  the mirror until the reject settles
- `cairn undo <agent-id>` - Write an `undo` signal; the daemon restores stable
  to its pre-accept state for that agent (the snapshot lives in the bin
  workspace under `undo/{agent_id}/` and is retained on the lifecycle cleanup
  schedule)

Mutating commands exit 2 with guidance when no daemon is running.

**Reference interpretation:**
- With `FileCodeProvider` (default): `reference` is a path to a Python script file
- With `LLMCodeProvider` (--provider llm): `reference` is natural language task description
- With `GitCodeProvider`: `reference` is a git URL with path (e.g., `git://github.com/org/repo:script.py`)
- With `RegistryCodeProvider`: `reference` is a registry URL (e.g., `registry://org/script-name:version`)

### Signal adapter contract (P1.4/P1.5)

Signals are the transport for CLI→daemon mutation commands.  The CLI writes
`$CAIRN_HOME/signals/{type}-{signal_id}.json` atomically (temp name + rename,
so the watcher never sees a partial file).  The daemon watches the directory
and also runs a periodic sweep (`SIGNAL_SWEEP_INTERVAL_SECONDS`) as a backstop
so a signal written during startup is not lost.

Processing claims a file by renaming it to `*.processing` (atomic — two
observers cannot both claim it), then dispatches.  Successful signals are
removed; failed signals are quarantined to `$CAIRN_HOME/signals/failed/` with
an `.error.txt` sidecar so failures are inspectable rather than silently
deleted.

## Documentation boundaries

To avoid drift:
- `../README.md`: setup + first commands only.
- `CONCEPT.md`: conceptual model and invariants only.
- `SPEC.md`: runtime details, contracts, and public APIs only.
- `.agents/skills/*`: implementation workflows that link back to these canonical docs.
