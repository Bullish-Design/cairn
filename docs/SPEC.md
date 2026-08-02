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
   - The bootstrap script shipped into the sandbox exposes the canonical capability surface
     (`read_file`, `write_file`, `list_dir`, `file_exists`, `search_files`, `search_content`,
     `submit_result`, `log`) as plain functions over the workspace directory.
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
- No network, no host filesystem, no other processes, no environment variables
  (``--clearenv``).
- File access is additionally confined to the workspace root by the sandbox API
  (absolute paths and ``..`` traversal are rejected).
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

Deletions re-import into the agent overlay for overlay-owned files; stable-only
files cannot be tombstoned with the current fsdantic overlay API (the sandbox
cannot delete files that exist only in stable).

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

### CLI contract (current)

CLI subcommands are a transport adapter: each invocation parses into a normalized `CairnCommand` and calls orchestrator `submit_command`.

- `cairn up [--provider PROVIDER]` - Start orchestrator with specified code provider
- `cairn spawn <reference> [--provider PROVIDER]` - High-priority task execution
- `cairn queue <reference> [--provider PROVIDER]` - Normal-priority task execution
- `cairn list-agents` - List all active agents
- `cairn status <agent-id>` - Show agent status and details
- `cairn accept <agent-id>` - Accept and merge agent changes
- `cairn reject <agent-id>` - Reject and discard agent changes

**Reference interpretation:**
- With `FileCodeProvider` (default): `reference` is a path to a Python script file
- With `LLMCodeProvider` (--provider llm): `reference` is natural language task description
- With `GitCodeProvider`: `reference` is a git URL with path (e.g., `git://github.com/org/repo:script.py`)
- With `RegistryCodeProvider`: `reference` is a registry URL (e.g., `registry://org/script-name:version`)

### Signal adapter contract

Signals are an optional transport adapter. When `enable_signal_polling=true`, the orchestrator watches `$CAIRN_HOME/signals/*.json` and routes each file through the same command parser + `submit_command` path used by CLI ingress. When disabled, signal parsing semantics remain identical for manual/explicit `process_signals_once` processing.

## Documentation boundaries

To avoid drift:
- `../README.md`: setup + first commands only.
- `CONCEPT.md`: conceptual model and invariants only.
- `SPEC.md`: runtime details, contracts, and public APIs only.
- `.agents/skills/*`: implementation workflows that link back to these canonical docs.
