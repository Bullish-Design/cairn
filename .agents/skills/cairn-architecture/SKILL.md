---
name: cairn-architecture
description: >-
  Orientation and mental model for the Cairn runtime: the "pile" metaphor, the
  four runtime layers (code providers, fsdantic storage, bubblewrap execution,
  orchestration), the data layout, the agent lifecycle state machine, and the
  canonical docs. Use when starting work in the Cairn repository or when you
  need to understand how the pieces fit together before making changes.
license: MIT
metadata:
  subsystem: core
---

# Cairn Architecture

Cairn is a **workspace-aware orchestration runtime for sandboxed code
execution**: execute code in isolated copy-on-write workspaces, preview the
changes, and let humans control integration with explicit accept/reject.

Canonical sources (read them when a detail matters):
- [README](../../../README.md) — setup + quickstart.
- [CONCEPT](../../../docs/CONCEPT.md) — philosophy and invariants.
- [SPEC](../../../docs/SPEC.md) — exact runtime contracts and public APIs.
- [MIGRATION](../../../docs/MIGRATION.md) — V1 → V2 changes.

> **Source-of-truth rule:** `docs/SPEC.md` defines runtime contracts; any
> change to `src/cairn/*` that alters behavior must update SPEC.md in the same
> change.

## The metaphor: a pile, not branches

A cairn is a pile of stones where each traveler adds to a shared structure.

- The **stable workspace** (`stable.db`) is the source of truth.
- Code executes in **isolated agent overlays** (`agent-*.db`) with copy-on-write
  semantics — writes never touch stable.
- Changes are **previewed** (the sandbox workdir doubles as the preview) before
  integration.
- Humans **accept** (merge overlay → stable) or **reject** (discard overlay).

## The four runtime layers

Everything in `src/cairn/` implements one of these contracts:

1. **Code Sourcing** — `cairn.providers.providers`
   - `CodeProvider` protocol: `get_code(reference, context) -> str` plus
     optional `validate_code(code) -> (bool, str | None)`.
   - Built-ins: `FileCodeProvider` (path to a `.py` script), `InlineCodeProvider`
     (reference *is* the code).
   - Plugins (`cairn-git`, `cairn-registry`) register entry points
     under the `cairn.providers` group and are loaded by name.

2. **Storage** — `cairn.runtime.workspace_manager`, fsdantic `Workspace`
   - Databases live under `<project>/.agentfs/`: `stable.db`, `bin.db`
     (lifecycle metadata + undo snapshots), `agent-{id}.db` (one per agent).
   - Access is via fsdantic workspace managers: `workspace.files`,
     `workspace.kv`, `workspace.overlay` (tombstones, merge), `workspace.materialize`.
   - Turso/SQLite-backed: WAL mode by default, optional MVCC, per-workspace
     connection serialization via a worker thread.

3. **Execution** — `cairn.runtime.sandbox`
   - `BwrapExecutor` runs the materialized workflow: **materialize** the agent
     overlay (over stable) to `$CAIRN_HOME/workspaces/{agent_id}`, **run** the
     code as stock CPython inside `bwrap` (only the workspace writable), then
     **re-import** the changeset into the agent overlay (added/changed files
     written, deletions recorded as overlay tombstones).
   - The sandbox API (`read_file`, `write_file`, `list_dir`, `file_exists`,
     `delete_file`, `search_files`, `search_content`, `submit_result`, `log`)
     is defined in `cairn.runtime.sandbox.boot` and injected as globals into
     task code. It is an **ergonomic convenience, not a security boundary** —
     bubblewrap is the boundary.
   - See the [cairn-task-code](../cairn-task-code/SKILL.md) skill.

4. **Orchestration** — `cairn.orchestrator`
   - `CairnOrchestrator`: owns the workspaces, the priority `TaskQueue`, a
     semaphore-bounded worker loop, and the lifecycle store.
   - `LifecycleStore`: typed agent metadata in `bin.db` KV (single canonical
     location) plus a JSON **lifecycle mirror** (`$CAIRN_HOME/state/lifecycle.json`)
     that the CLI reads (pyturso locks `bin.db` even for read-only opens).
   - `SignalHandler`: the CLI → daemon transport. Mutating CLI commands write
     signal files under `$CAIRN_HOME/signals/`; the daemon watches + sweeps,
     claims by atomic rename to `*.processing`, dispatches, then removes
     (or quarantines failures to `signals/failed/`).
   - `FileWatcher`: mirrors the project tree into `stable` once at startup
     (`initial_sync`) and continuously after.

## Data layout contract

```text
$PROJECT_ROOT/.agentfs/
├── stable.db                     # source of truth
├── bin.db                        # lifecycle KV + undo/{agent_id}/ snapshots
└── agent-{id}.db                 # per-agent overlay

$CAIRN_HOME/ (default ~/.cairn)
├── workspaces/{agent_id}/        # sandbox workdir == review preview
│   └── .cairn/
│       ├── task.py               # agent code (provider output)
│       ├── task.json             # inputs (task_description)
│       ├── boot.py               # sandbox bootstrap (shipped in)
│       ├── submission.json       # submit_result payload
│       └── run.log               # sandbox stdout/stderr
├── signals/                      # CLI → daemon commands (+ failed/ quarantine)
└── state/
    ├── orchestrator.pid          # daemon pidfile
    ├── lifecycle.json            # lifecycle mirror (CLI read path)
    └── orchestrator.json         # queue stats snapshot
```

## Agent lifecycle

```
QUEUED → GENERATING → EXECUTING → SUBMITTING → REVIEWING → (ACCEPTED | REJECTED | ERRORED)
```

- `QUEUED`: task in the priority queue, worker not yet started.
- `GENERATING`: provider `get_code` + `validate_code`.
- `EXECUTING`: `BwrapExecutor.run` (materialize → sandbox → re-import).
- `SUBMITTING`: submission persisted to agent workspace KV.
- `REVIEWING`: awaiting human accept/reject. **Terminal for a successful run.**
- `ACCEPTED`: overlay merged into stable (staleness check unless `--force`).
- `REJECTED`: overlay discarded (also allowed from QUEUED/ERRORED).
- `ERRORED`: any provider/execution/lifecycle failure; daemon crash mid-run
  marks `GENERATING`/`EXECUTING`/`SUBMITTING` agents ERRORED on recovery.

State transitions are validated by `VALID_TRANSITIONS` in
`cairn.runtime.agent` (`AgentContext.transition` raises `AgentStateError` on
invalid moves).

## Key invariants

1. **The daemon owns the databases.** The CLI never constructs an orchestrator
   for a subcommand: mutations go through signal files, queries read the
   lifecycle mirror.
2. **Stable is never mutated without explicit human acceptance** — the accept
   merge is the only writer of stable (besides the watcher mirroring local
   file edits, which is the same thing).
3. **Bubblewrap is the security boundary.** Task code is ordinary Python with
   the full stdlib; anything that must not be reachable must be excluded at
   the mount layer.
4. **Accept is safe by default.** Without `--force`, accept is refused
   (`ACCEPT_STALE_BASE`) if stable changed for any path the agent touched
   since the agent read it; accept is reversible via `cairn undo` (snapshots
   in `bin.db` under `undo/{agent_id}/`).

## Current source tree map

```text
src/cairn/
├── cli/
│   ├── cli.py          # argparse CLI (thin client: signals + mirror)
│   ├── typer_cli.py    # Typer CLI (workspace/files/agent/preview groups)
│   └── commands.py     # typed command models + parse/dispatch
├── core/
│   ├── constants.py    # all magic numbers / limits
│   ├── exceptions.py   # typed error hierarchy with error codes
│   └── types.py        # TypedDicts (SubmissionData, ...)
├── orchestrator/
│   ├── orchestrator.py # CairnOrchestrator: lifecycle + accept/reject/undo
│   ├── lifecycle.py    # LifecycleStore, records, mirror, retention
│   ├── queue.py        # priority TaskQueue
│   ├── signals.py      # SignalHandler + write_signal
│   ├── daemon.py       # pidfile claim/liveness
│   └── orchestrator_helpers.py
├── providers/
│   └── providers.py    # CodeProvider protocol, file/inline, entry points
├── runtime/
│   ├── agent.py        # AgentState, AgentContext, VALID_TRANSITIONS
│   ├── settings.py     # Orchestrator/Executor/Paths settings (CAIRN_* env)
│   ├── workspace_manager.py  # open_workspace, WorkspaceManager
│   ├── inspection.py   # WorkspaceInspector, WorkspaceStats
│   ├── state.py        # AgentStateManager (namespaced KV)
│   ├── workspace_cache.py    # LRU workspace cache with pinning
│   └── sandbox/
│       ├── sandbox.py  # BwrapExecutor: materialize → run → re-import
│       └── boot.py     # sandbox API + rlimits (shipped into the sandbox)
├── utils/
│   ├── retry.py        # with_retry decorator, RetryStrategy
│   └── error_formatting.py
└── watcher/
    └── watcher.py      # FileWatcher: project → stable sync
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
