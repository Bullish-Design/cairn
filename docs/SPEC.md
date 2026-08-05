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
     - `GitCodeProvider` (cairn-git) - loads code from git repositories
     - `RegistryCodeProvider` (cairn-registry) - fetches code from registries
   - The orchestrator accepts any `CodeProvider` implementation via constructor parameter.
   - Provider `context` is `{"agent_id": str, "workspace": ProjectView, "project_root": Path}` —
     the workspace entry is a **read-only** snapshot view over the canonical
     tree (gitignore-aware, no symlink following); providers never receive a
     writable workspace or database (review §3.5).

2. **Repository snapshot + disposable workspaces (`cairn.runtime.repo`)**
   - The **actual Git working tree is the canonical source of truth**; there is no
     `stable.db` file mirror (review §4.2).  SQLite/fsdantic stores orchestration
     metadata only (`bin.db`, per-agent `agent-*.db`), never file contents.
   - `ProjectFilter` — a gitignore-aware inclusion predicate confined beneath the
     repository root: never follows symlinks and never admits `.git`/`.hg`/`.jj`,
     `.cairn` scaffolding, `.agentfs`, or ignored paths.
   - `capture_manifest(root)` — a faithful point-in-time snapshot: existence, kind
     (file/dir/symlink), sha256 digest, permission bits, and symlink target.
     Absence is an explicit state (a path not in the manifest did not exist).
   - `materialize_workspace(src, dst)` — creates the disposable real directory the
     agent runs over (copy-on-write/reflink where the filesystem supports it),
     preserving modes, symlinks (as symlinks), and empty directories.

3. **Execution (`BwrapExecutor` over a disposable real workspace)**
   - Per task the executor captures the base manifest, materializes a disposable
     copy of the tree at `$CAIRN_HOME/workspaces/{agent_id}/`, writes the code to
     `.cairn/task.py`, and runs it as stock CPython inside a bubblewrap sandbox:
     only the disposable directory is writable; the interpreter runtime is mounted
     read-only; network, host filesystem, and other processes are unshared.
   - After execution the workspace is captured again and compared with the base
     manifest: the computed changeset (written/deleted/mode-changed paths) is the
     **authoritative record** of what the agent did.  There is no overlay database
     and no host-side re-import; the agent's `submit_result` prose is advisory.
   - Execution limits (wall-clock timeout, memory, CPU, recursion) are enforced via
     the sandbox bootstrap (rlimits) and host-side subprocess timeout; the total
     workspace budget is checked against the computed changeset after the run.

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

> **Source-of-truth note:** If runtime behavior in code and this section differ, update this section and the implementing modules together in the same change (`src/cairn/orchestrator/orchestrator.py`, `src/cairn/providers/providers.py`, `src/cairn/runtime/sandbox/sandbox.py`, `src/cairn/runtime/repo.py`).

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
├── agent-{id}.db        # per-agent metadata KV (run record, submission) — never file contents
└── bin.db               # lifecycle KV + undo snapshots + ACCEPTING journal

$CAIRN_HOME/ (default ~/.cairn)
├── workspaces/
│   └── {agent_id}/          # disposable real workspace == review surface
│       ├── .cairn/
│       │   ├── task.py          # Generated/loaded agent code
│       │   ├── task.json        # Task inputs (task_description)
│       │   ├── boot.py          # Sandbox bootstrap (shipped in)
│       │   ├── submission.json  # submit_result payload
│       │   └── run.log          # Sandbox stdout/stderr
│       └── ...                  # Materialized copy of the working tree
└── state/
    ├── orchestrator.sock   # daemon control socket (ownership + transport)
    └── lifecycle.json      # CLI read path
```

4. **Repository snapshot (no live mirror)**
   - The working tree is the canonical source of truth; there is no background
     watcher copying it into a database.
   - Per task the executor captures a base manifest of the tree (gitignore-aware,
     no symlink following, `.git`/`.cairn`/`.agentfs` never admitted) and
     materializes a disposable real copy; the computed diff against that manifest
     is the authoritative changeset.
   - At accept time the base is revalidated against a fresh manifest under the
     project integration lock; any discrepancy (including a missing run record)
     fails the gate closed.

5. **Daemon ownership (P1.4)**
   - The daemon owns the metadata databases and the control socket.  The CLI
     never constructs an orchestrator: mutations are sent over the Unix-socket
     transport (see the transport contract), queries read the lifecycle mirror.

## Repository manifest contracts (`cairn.runtime.repo`)

### Manifest fidelity

- Every admissible path is recorded with its kind: `file` (sha256 digest, size,
  permission bits), `dir` (permission bits, including empty directories), or
  `symlink` (raw `readlink` target, permission bits).  A path absent from the
  manifest is explicitly absent.
- Symlinks are **never dereferenced**: entries are read with `lstat`, files with
  `O_NOFOLLOW`.  A repo symlink pointing outside the project is recorded as a
  symlink entry; its target content never enters the manifest or the workspace.
- Admissibility: gitignore rules (root plus nested, deepest pattern wins),
  VCS metadata (`.git`/`.hg`/`.svn`/`.jj`), Cairn scaffolding (`.cairn`,
  `.agentfs`), and developer-environment dirs are excluded; `.pyc`/`.pyo` are
  excluded by suffix.

### Disposable workspace

- `materialize_workspace` copies only admissible paths, using copy-on-write
  reflinks where the filesystem supports them.  Modes, symlinks, and empty
  directories are preserved byte-for-byte.

### Changeset semantics

- `diff_manifests(base, current)` yields `added` (absent at base, present now),
  `removed` (present at base, absent now), `modified` (content/kind change,
  e.g. file digest or file→symlink), and `mode_changed` (permission-only drift).
- The executor maps this to the run record's `written`/`deleted` lists; the
  agent's `submit_result(changed_files=...)` claim is advisory only and is
  cross-checked against the computed set.

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
- Symlinks in the workspace are never followed by the host-side apply: a path
  the agent replaced with a symlink is recreated as a symlink, and host reads
  use `O_NOFOLLOW`/`lstat` throughout.

### Changeset application (accept)

The computed changeset from the run record is applied to the actual working
**tree** — not a database: written paths are copied from the disposable
workspace (symlinks recreated as symlinks, modes preserved), deleted paths are
removed, `mode_changed` permissions are applied, executable bits and empty
directories are recreated.  The whole mutation runs under one per-project
integration lock (``flock`` on ``.agentfs/integration.lock``):

1. revalidate every touched base entry against a fresh manifest of the tree
   (any discrepancy — including a missing run record — fails the gate closed
   with `ACCEPT_STALE_BASE`);
2. write a durable `ACCEPTING` journal entry in `bin.db`;
3. snapshot pre-apply content of every touched path into `bin.db` under
   `undo/{agent_id}/` (with post-apply digests recorded for undo validation);
4. apply strictly — any failure raises `WORKSPACE_MERGE_FAILED` and the
   journal is aborted; a process crash mid-apply is rolled back from the
   snapshot on the next startup (the tree never stays half-applied).

`cairn undo <agent-id>` runs under the same lock and first validates that the
accepted state is still present (via the post-apply digests); if the tree was
changed since the accept it refuses with `UNDO_STALE_BASE` and keeps the undo
record — it never overwrites later human edits and never reports success for
a partial undo.

### Toolchain closure (M8)

The disposable workspace mounts only the stdlib interpreter by default; a
declarative toolchain extends it read-only via two mechanisms:

- `CAIRN_EXECUTOR_SANDBOX_CLOSURE_PATH` — a file listing Nix store paths (one
  per line); the executor binds exactly those paths read-only.  Add git, a
  compiler, or a test runner's store closure here to give agents repo tooling.
- `ExecutorSettings.runtime_mounts` — explicit `(src, dst)` read-only binds
  (e.g. a venv or a toolchain directory).

Only the disposable workspace is ever writable; every toolchain bind is
read-only.

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

### Pre-flight validation

Provider-level `validate_code` is the only pre-flight gate (no check-time
declared external validation). Syntax errors surface as sandbox tracebacks that
mark the agent ERRORED.

Validation errors prevent execution and transition agent to ERRORED state.

## Iterative agent driver (review §4.3)

The one-shot script is replaced by an iterative driver contract
(`cairn.runtime.driver`):

- `WorkspaceCapability` — the narrow capability a driver (or its model
  client) receives: read/list/search, write/delete **within the bounded
  workspace only**, and `run` through the sandbox runner.  Paths are
  validated (no absolute paths, no `..`), and host execution is impossible
  without a sandbox runner.
- `IterativeDriver` — the protocol: `run(task, capability, *, step_limit)`
  returns the submission.  `ScriptedDriver` is the reference implementation
  (explicit step plan with a hard step limit).
- Drivers that run inside the sandbox use the sandbox API plus ordinary
  subprocess execution for tests (plain Python); the capability class is the
  host-side/embedding view.

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

- accept normalized `CairnCommand` ingress and dispatch to command handlers (`queue/accept/reject/status/list_agents/undo`),
- treat the CLI transport and the command model as the single dispatch path (every request parses into the same command model before dispatch),
- enqueue tasks into a priority queue,
- run a long-lived worker loop that acquires an `asyncio.Semaphore(max_concurrent_agents)` slot before starting each task; the loop survives per-iteration failures (backoff) and is restarted by a supervisor if it exits unexpectedly,
- release the semaphore slot in one completion `finally` path,
- use `CodeProvider` to source executable code (from files, git, registries, or custom providers),
- validate code via provider `validate_code()` before execution,
- write code to `$CAIRN_HOME/workspaces/{agent_id}/.cairn/task.py`,
- execute code via `BwrapExecutor` (capture base manifest → materialize disposable
  workspace → sandbox run → compute changeset from the diff),
- keep the disposable workspace as the immutable review surface (workdir doubles as preview),
- persist the computed run record (written/deleted/mode-changed paths, base hashes, log) and
  lifecycle metadata to the canonical KV stores on every state transition, plus a JSON mirror for CLI reads,
- persist queue stats snapshot under `$CAIRN_HOME/state/` (stats only, not agent metadata),
- fail agents that were mid-run when the daemon died (`GENERATING`/`EXECUTING`/`SUBMITTING` → `ERRORED` with an explanation; optionally re-queued with `requeue_interrupted`),
- keep the workdir and partial run log when a run fails (debuggability; cleaned by retention or `cairn reject`).
- apply accepted changesets to the actual working tree under a fresh base revalidation (fail closed),
  with pre-apply content snapshotted for `cairn undo`.

### CLI contract (thin client, P1.4)

The daemon owns the databases and the control socket.  The CLI is a thin
client: it never constructs an orchestrator.  Mutating commands are sent over
the Unix-socket transport and return their result synchronously; query
commands read the daemon's lifecycle mirror (`$CAIRN_HOME/state/lifecycle.json`)
read-only — pyturso 0.7.2 locks database files exclusively even for read-only
opens, so the CLI cannot open `bin.db` while the daemon holds it.

- `cairn up` - Start the daemon (claims `$CAIRN_HOME/state/orchestrator.pid`; a
  second `cairn up` in the same `CAIRN_HOME` is refused)
- `cairn run <task> [--timeout N]` - Run a single task inline to completion
  (no daemon; refused while a daemon is running)
- `cairn spawn <reference>` - High-priority task; sends a `spawn` request
- `cairn queue <reference>` - Normal-priority task; sends a `queue` request
- `cairn list-agents` - Read the lifecycle mirror
- `cairn status <agent-id>` - Read the lifecycle mirror; exit 1 + friendly
  message for unknown agents (no traceback)
- `cairn accept <agent-id> [--timeout N] [--force]` - Send an `accept` request and
  return the result synchronously.  Without `--force`, accept revalidates every touched
  base entry against the current working tree and is refused (`ACCEPT_STALE_BASE`) on any
  discrepancy, including a missing run record.
- `cairn reject <agent-id> [--timeout N]` - Send a `reject` request and return the result
- `cairn undo <agent-id>` - Send an `undo` request; the daemon restores the working tree
  to its pre-accept state for that agent (the snapshot lives in the bin
  workspace under `undo/{agent_id}/` and is retained on the lifecycle cleanup
  schedule)

Mutating commands exit 2 with guidance when no daemon is running.

**Reference interpretation:**
- With `FileCodeProvider` (default): `reference` is a path to a Python script file
- With `GitCodeProvider`: `reference` is a git URL with path (e.g., `git://github.com/org/repo:script.py`)
- With `RegistryCodeProvider`: `reference` is a registry URL (e.g., `registry://org/script-name:version`)

### Transport contract (P1.4/P1.5, review §3.1)

The CLI↔daemon boundary is a **Unix-domain socket with a persisted command
table** — not signal files:

- The daemon binds `$CAIRN_HOME/state/orchestrator.sock`; the socket bind is
  the daemon-ownership primitive (a second daemon cannot bind a live socket;
  a stale socket file from a crash is detected by a failed connect probe and
  unlinked before rebinding).  The pidfile is informational only.
- Mutating commands are sent as a single JSON request carrying a
  client-generated `command_id`; the daemon responds with the result payload
  or the error — synchronous feedback, no polling, no five-minute stale
  accepts.
- Every dispatch is recorded in `bin.db` (`CommandRecord`): a retried
  `command_id` returns the recorded result instead of re-executing
  (idempotent dispatch), and a "pending" record on startup was in flight
  when the daemon died and is failed by recovery.
- All state/mirror files are written with a unique temporary file + `fsync`
  + `os.replace`, so concurrent producers can never collide on a fixed
  `.tmp` name (review §3.8).

## Documentation boundaries

To avoid drift:
- `../README.md`: setup + first commands only.
- `CONCEPT.md`: conceptual model and invariants only.
- `SPEC.md`: runtime details, contracts, and public APIs only.
- `.agents/skills/*`: implementation workflows that link back to these canonical docs.

## Retry utilities

`with_retry` lives in `cairn.utils.retry`; `cairn.utils.retry_utils` is a
one-release deprecated alias.

## CLI entry points

There is one CLI: `cairn` (argparse).  It implements the thin-client
contract: daemon commands send requests over the transport / read the
lifecycle mirror, and no subcommand constructs an orchestrator.  Commands:
`up`, `run`, `spawn`, `queue`, `list-agents`, `status`, `accept`, `reject`,
`undo`, `logs`, plus the `workspace`, `files`, and `preview` groups.  The
`--project-root`/`--cairn-home`/provider flags work on every command.
Managed workspaces (`stable`, `bin`, `agent-*`, `bin-*`) are never writable
through the CLI, and workspace names are validated against traversal
(review §2.8).
