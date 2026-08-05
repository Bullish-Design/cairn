---
name: cairn-architecture
description: >-
  Orientation and mental model for the Cairn runtime: the canonical working
  tree, the four runtime layers (code providers, repository snapshots,
  bubblewrap execution, orchestration), the data layout, the agent lifecycle
  state machine, and the canonical docs. Use when starting work in the Cairn
  repository or when you need to understand how the pieces fit together before
  making changes.
license: MIT
metadata:
  subsystem: core
---

# Cairn Architecture

Cairn is a **repository-agent orchestration runtime**: execute code in
disposable real workspaces, preview the authoritative changeset, and let
humans control integration with explicit accept/reject.

Canonical sources (read them when a detail matters):
- [README](../../../README.md) — setup + quickstart.
- [CONCEPT](../../../docs/CONCEPT.md) — philosophy and invariants.
- [SPEC](../../../docs/SPEC.md) — exact runtime contracts and public APIs.

> **Source-of-truth rule:** `docs/SPEC.md` defines runtime contracts; any
> change to `src/cairn/*` that alters behavior must update SPEC.md in the same
> change.

## The metaphor: a pile, not branches

A cairn is a pile of stones where each traveler adds to a shared structure.

- The **actual Git working tree** is the canonical source of truth.
- Code executes in **disposable real workspaces** (copy-on-write where the
  filesystem supports it) — writes never touch the tree.
- The **computed changeset** (files/dirs/symlinks/modes changed) is previewed
  before integration; the agent's summary is advisory, the diff is truth.
- Humans **accept** (apply the changeset to the tree under a project lock) or
  **reject** (discard the workspace); every accept is reversible via `cairn
  undo`.

## The four runtime layers

Everything in `src/cairn/` implements one of these contracts:

1. **Code Sourcing** — `cairn.providers.providers`
   - `CodeProvider` protocol: `get_code(reference, context) -> str` plus
     `validate_code(code) -> (bool, str | None)` (called unconditionally).
   - Built-ins: `FileCodeProvider` (path to a `.py` script), `InlineCodeProvider`
     (reference *is* the code).
   - Plugins (`cairn-git`, `cairn-registry`) register entry points under the
     `cairn.providers` group and are loaded by name. The provider is chosen
     when the daemon starts (`cairn up --provider`) or for inline runs
     (`cairn run --provider`).
   - Provider `context` carries `{"agent_id", "workspace": ProjectView,
     "project_root"}` — the workspace entry is a **read-only** gitignore-aware
     view over the tree, never a writable database (review §3.5).

2. **Repository snapshots + disposable workspaces** — `cairn.runtime.repo`
   - The canonical tree is captured per task with `capture_manifest`:
     existence, kind (file/dir/symlink), sha256 digest, mode, symlink target;
     gitignore-aware, never follows symlinks, `.git`/`.cairn`/`.agentfs` and
     dev-environment dirs excluded; absence is an explicit state.
   - `materialize_workspace` creates the disposable real directory the agent
     runs over (reflink/copy-on-write where supported), preserving modes,
     symlinks, and empty directories.
   - `diff_manifests` yields added/removed/modified/mode-changed — the basis
     of the authoritative changeset.

3. **Execution** — `cairn.runtime.sandbox`
   - `BwrapExecutor` snapshots the tree, materializes the disposable
     workspace, runs the code as stock CPython inside `bwrap` (only the
     workspace writable), and computes the authoritative changeset from the
     diff. There is no overlay database and no host-side re-import.
   - Output is streamed into a capped buffer (the task is killed past
     `max_log_bytes`), and the workspace byte/file budget is sampled during
     the run — the host cannot be exhausted by a log-spammer or disk-filler.
   - The sandbox API (`read_file`, `write_file`, `list_dir`, `file_exists`,
     `delete_file`, `search_files`, `search_content`, `submit_result`, `log`)
     is defined in `cairn.runtime.sandbox.boot` and injected as globals into
     task code. It is an **ergonomic convenience, not a security boundary** —
     bubblewrap is the boundary.
   - Iterative agents use `cairn.runtime.driver` (narrow `WorkspaceCapability`
     + `IterativeDriver` protocol).

4. **Orchestration** — `cairn.orchestrator`
   - `CairnOrchestrator`: owns the metadata workspaces, the priority
     `TaskQueue`, a semaphore-bounded worker loop, and the lifecycle store.
   - `LifecycleStore`: typed agent metadata in `bin.db` KV (single canonical
     location) plus a JSON **lifecycle mirror** (`$CAIRN_HOME/state/lifecycle.json`)
     that the CLI reads (pyturso locks `bin.db` even for read-only opens).
   - `OrchestratorTransport`: the CLI ↔ daemon boundary is a **Unix-domain
     socket** (`$CAIRN_HOME/state/orchestrator.sock`) with a persisted command
     table — command IDs, idempotent dispatch, in-flight recovery, and
     synchronous results (no signal files).
   - Accept/undo run under one per-project integration lock (`flock` on
     `.agentfs/integration.lock`) with a durable `ACCEPTING` journal and
     pre-apply undo snapshots.

## Data layout contract

```text
$PROJECT_ROOT/.agentfs/
├── bin.db                 # lifecycle KV + undo/{agent_id}/ snapshots + ACCEPTING journal + command table
├── integration.lock       # flock: the single writer gate for tree mutation
└── agent-{id}.db          # per-agent metadata KV (run record, submission)

$CAIRN_HOME/ (default ~/.cairn)
├── workspaces/{agent_id}/ # disposable real workspace == review surface
│   └── .cairn/            # task.py, task.json, boot.py, submission.json, run.log
├── state/
│   ├── orchestrator.sock  # daemon control socket (ownership + transport)
│   ├── lifecycle.json     # CLI read path
│   ├── orchestrator.json  # queue stats snapshot
│   └── orchestrator.pid   # informational only (ownership is the socket)
└── signals/               # gone: replaced by the socket transport
```

## Agent lifecycle

```
QUEUED → GENERATING → EXECUTING → SUBMITTING → REVIEWING → (ACCEPTED | REJECTED | ERRORED)
```

- `QUEUED`: task in the priority queue, worker not yet started.
- `GENERATING`: provider `get_code` + `validate_code`.
- `EXECUTING`: `BwrapExecutor.run` (snapshot → materialize → sandbox → diff).
- `SUBMITTING`: submission persisted to agent workspace KV.
- `REVIEWING`: awaiting human accept/reject. **Terminal for a successful run.**
- `ACCEPTED`: changeset revalidated under the integration lock and applied to
  the real working tree; pre-apply content snapshotted for `cairn undo`.
- `REJECTED`: disposable workspace discarded (also allowed from QUEUED/ERRORED).
- `ERRORED`: any provider/execution/lifecycle failure; daemon crash mid-run
  marks `GENERATING`/`EXECUTING`/`SUBMITTING` agents ERRORED on recovery, and
  an interrupted accept is rolled back from its journal + snapshot.

State transitions are validated by `VALID_TRANSITIONS` in
`cairn.runtime.agent` (`AgentContext.transition` raises `AgentStateError` on
invalid moves).

## Key invariants

1. **The actual Git working tree is canonical.** No database mirrors file
   contents; SQLite/fsdantic holds orchestration metadata only.
2. **The daemon owns the metadata databases and the control socket.** The CLI
   never constructs an orchestrator: mutations go over the socket transport,
   queries read the lifecycle mirror.
3. **Bubblewrap is the security boundary.** Task code is ordinary Python with
   the full stdlib; anything that must not be reachable must be excluded at
   the mount layer. Containment is proportional, not perfect — kernel escape
   resistance is out of scope.
4. **Accept is fail-closed and reversible.** The base every touched path had
   at run start is revalidated against the current tree under the project
   lock (a missing run record fails closed); undo validates the accepted state
   is still present before reverting.
5. **The computed diff is truth.** `submit_result` prose is advisory; the
   changeset computed from the workspace diff drives review, apply, and undo.

## Current source tree map

```text
src/cairn/
├── cli/
│   ├── cli.py          # the single CLI (argparse; agent + workspace/files/preview)
│   └── commands.py     # typed command models + parse/dispatch
├── core/
│   ├── constants.py    # all magic numbers / limits
│   ├── exceptions.py   # typed error hierarchy with error codes
│   └── types.py        # TypedDicts (SubmissionData, ...)
├── orchestrator/
│   ├── orchestrator.py # CairnOrchestrator: lifecycle + accept/reject/undo
│   ├── lifecycle.py    # LifecycleStore, records, mirror, retention
│   ├── queue.py        # priority TaskQueue
│   ├── transport.py    # Unix-socket transport + command table (replaces signals)
│   ├── daemon.py       # socket-based daemon liveness + informational pidfile
│   └── orchestrator_helpers.py
├── providers/
│   └── providers.py    # CodeProvider protocol, file/inline, entry points
├── runtime/
│   ├── repo.py         # ProjectFilter, capture_manifest, materialize, diff
│   ├── driver.py       # WorkspaceCapability, IterativeDriver, ProjectView
│   ├── integration.py  # IntegrationLock (flock)
│   ├── agent.py        # AgentState, AgentContext, VALID_TRANSITIONS
│   ├── settings.py     # Orchestrator/Executor/Paths settings (CAIRN_* env)
│   ├── workspace_manager.py  # open_workspace, WorkspaceManager
│   ├── inspection.py   # WorkspaceInspector, WorkspaceStats
│   ├── state.py        # AgentStateManager (namespaced KV)
│   ├── workspace_cache.py    # LRU workspace cache with reference-counted pins
│   └── sandbox/
│       ├── sandbox.py  # BwrapExecutor: snapshot → materialize → run → diff
│       └── boot.py     # sandbox API + rlimits (shipped into the sandbox)
├── utils/
│   ├── retry.py        # with_retry decorator, RetryStrategy
│   └── error_formatting.py
└── watcher/             # deleted with stable.db (the mirror died in M3)
```

## Reading order for a contributor

1. `README.md` — install + quickstart.
2. [CONCEPT](../../../docs/CONCEPT.md) — intent and invariants.
3. [SPEC](../../../docs/SPEC.md) — exact contracts.
4. The subsystem skill that matches the task, then the code it names.

## Related skills

- [cairn-task-code](../cairn-task-code/SKILL.md) — writing sandbox task scripts.
- [cairn-code-providers](../cairn-code-providers/SKILL.md) — pluggable code sources.
- [cairn-library-api](../cairn-library-api/SKILL.md) — embedding Cairn as a library.
- [cairn-cli-operations](../cairn-cli-operations/SKILL.md) — running/debugging via CLI.
- [cairn-contribution](../cairn-contribution/SKILL.md) — modifying the repo itself.
