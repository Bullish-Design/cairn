---
name: cairn-cli-operations
description: >-
  Operating a Cairn deployment from the command line: starting the daemon
  (cairn up) or running inline (cairn run), submitting work (spawn/queue),
  the review flow (list-agents, status, accept with staleness checks, reject,
  undo, logs), the Unix-socket transport and lifecycle mirror, exit codes,
  and troubleshooting common failures. Use when running or debugging Cairn
  via CLI.
license: MIT
metadata:
  subsystem: cli
---

# Cairn CLI Operations

One CLI ships with the package: `cairn` (argparse). It implements the same
**thin-client contract**: the daemon owns the metadata databases and the
control socket; mutating commands are sent over the Unix-socket transport and
return their result synchronously; query commands read the daemon's lifecycle
mirror read-only. **No subcommand constructs an orchestrator.**

Canonical references: [CLI_README](../../../docs/CLI_README.md) and
[SPEC § CLI contract](../../../docs/SPEC.md).

## Core commands

| Command | Purpose | Notes |
|---|---|---|
| `cairn up` | Start the daemon (binds `~/.cairn/state/orchestrator.sock`; a second `cairn up` for the same `CAIRN_HOME` is refused) | Long-running; owns the metadata databases |
| `cairn run <task> [--timeout N]` | Run one task inline to completion, no daemon | Refused while a daemon runs; exit 0 if REVIEWING, else 1 |
| `cairn spawn <reference>` | High-priority task (`TaskPriority.HIGH`) | Mutating → socket request |
| `cairn queue <reference>` | Normal-priority task (`TaskPriority.NORMAL`) | Mutating → socket request |
| `cairn list-agents` | Read the lifecycle mirror | Query |
| `cairn status <agent-id>` | Read the mirror; unknown agent → exit 1, friendly message, no traceback | Query |
| `cairn accept <agent-id> [--timeout N] [--force]` | Socket request; returns the result synchronously | Staleness check unless `--force` |
| `cairn reject <agent-id> [--timeout N]` | Socket request; returns the result synchronously | Allowed from REVIEWING, QUEUED, ERRORED |
| `cairn undo <agent-id>` | Restore the working tree to pre-accept state | Needs the undo snapshot; validates the accepted state first |
| `cairn logs <agent-id>` | Print the sandbox run log | Works for errored agents too |
| `cairn workspace create/list/info/delete` | User metadata workspaces under `.agentfs` | Managed names (`stable`, `bin`, `agent-*`, `bin-*`) are refused |
| `cairn files list/read/write/search/tree` | Inspect user workspaces | Read-only unless `write`; managed names refused |
| `cairn preview changes/file` | The review surface: disposable workspace vs current tree | Diff of added/modified/removed/mode-changed paths |

**Exit codes:** `0` success; `1` unknown agent or accept/reject/undo failed;
`2` mutating command with no daemon running (with guidance to run `cairn up`
or `cairn run`).

Common flags on every command: `--project-root`, `--cairn-home`,
`--provider <file|inline|plugin>`, `--provider-base-path`, plus executor
flags (`--max-execution-time`, `--max-memory-bytes`, `--max-recursion-depth`).
The provider is chosen **when the daemon starts** (`cairn up --provider`) or
for inline runs (`cairn run --provider`); mutating commands ignore it.

## How the plumbing works

1. **Transport (mutations).** The CLI connects to
   `$CAIRN_HOME/state/orchestrator.sock` and sends one JSON request carrying a
   command type, payload, and a client-generated `command_id`; the daemon
   responds with the result or the error. Every dispatch is recorded in
   `bin.db` (`CommandRecord`), so a retried `command_id` returns the recorded
   outcome (idempotent) and a command that was in flight when the daemon died
   is failed by startup recovery.
2. **Lifecycle mirror (queries).** The daemon rewrites
   `$CAIRN_HOME/state/lifecycle.json` after every lifecycle mutation (unique
   temp file + `fsync` + `os.replace`). The CLI reads only that file — it
   can't open `bin.db` while the daemon holds it (pyturso takes an exclusive
   file lock even for read-only opens).
3. **Ownership.** The daemon bind of the control socket *is* the ownership
   primitive: a second daemon cannot bind a live socket, and a stale socket
   file from a crash is detected by a failed connect probe and reclaimed.
   `$CAIRN_HOME/state/orchestrator.pid` is informational only.

## The review flow

```bash
cairn up &                                  # 1. start the daemon
cairn queue scripts/refactor_imports.py     # 2. submit work
cairn list-agents                           # 3. find the agent id + state
cairn status agent-1a2b3c4d                 # 4. inspect (state, error, submission)
cairn logs agent-1a2b3c4d                   #    failed/odd run? read the sandbox log
cairn accept agent-1a2b3c4d                 # 5a. apply the changeset to the working tree
# or
cairn reject agent-1a2b3c4d                 # 5b. discard the workspace
# or, to reverse a (possibly wrong) accept:
cairn undo agent-1a2b3c4d
```

### Accept semantics

- The computed changeset (from the run record) is applied to the **actual
  working tree** — the canonical source of truth.
- **Staleness check (default):** every touched base entry is revalidated
  against a fresh manifest of the tree under the project integration lock;
  any discrepancy — content, mode, type, symlink-target, an absent-at-start
  path that now exists, or a **missing run record** — refuses with
  `ACCEPT_STALE_BASE`. `--force` bypasses this (you may silently discard a
  concurrent edit — use with care).
- Before applying, pre-apply content of every touched path is snapshotted
  into `bin.db` under `undo/{agent_id}/` (with post-apply digests), so
  `cairn undo <agent-id>` restores the tree exactly.
- A crash mid-apply is rolled back from the snapshot on the next startup
  (durable `ACCEPTING` journal).

### Reject semantics

Allowed from `REVIEWING`, `QUEUED` (drops the queued entry so the worker never
dequeues a phantom), and `ERRORED`. The disposable workspace and metadata are
discarded.

### Undo semantics

`cairn undo <agent-id>` runs under the same project lock and first validates
that the accepted state is still present (post-apply digests); if the tree
changed since the accept it refuses with `UNDO_STALE_BASE` and keeps the undo
record — it never overwrites later human edits and never reports success for
a partial undo.

### Status claims vs. ground truth

`cairn status` prints `files_written`, `files_deleted`, and `claim_mismatch`.
If the agent's `submit_result(changed_files=...)` disagrees with the computed
changeset (even an empty claim), status prints both lists plus
`! the agent's self-report does not match what it did` on stderr. Trust the
ground-truth lists.

## Reference interpretation

With `FileCodeProvider` (default) `reference` is a **path to a Python script**
(project-relative). `git`/`registry` providers accept their own schemes — see
[cairn-code-providers](../cairn-code-providers/SKILL.md).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `cairn queue` exits 2, "No Cairn daemon is running" | No daemon for this `CAIRN_HOME`. Start `cairn up`, or use `cairn run <task>` inline. |
| `cairn run` refuses with "A daemon is running" | `cairn run` is single-process; use `cairn queue`/`spawn` instead. |
| `cairn up` refuses: "A Cairn daemon is already running" | The control socket is live (bind refused). A stale socket from a crash is reclaimed automatically. |
| Agent stuck QUEUED and never runs | Worker loop error — check daemon logs; the worker survives per-iteration failures with backoff and is restarted by a supervisor if it exits. |
| Agent ERRORED after daemon restart | Crash recovery: mid-run agents (`GENERATING`/`EXECUTING`/`SUBMITTING`) are failed with "Interrupted by orchestrator restart", and interrupted accepts are rolled back. Re-queue with `requeue_interrupted=True` if desired. |
| `cairn status <id>` → "Unknown agent" | Agent may have been cleaned by retention (7 days) or trashed. Check `cairn list-agents`. |
| `cairn accept` → `accept failed: ...` | The agent's record shows the error (e.g. `ACCEPT_STALE_BASE`). Fix the conflict or pass `--force` knowingly. |
| `cairn undo` → `UNDO_NOT_FOUND` | No snapshot: never accepted, or expired by retention. |
| `cairn undo` → `UNDO_STALE_BASE` | The tree changed since the accept; resolve the drift, then undo again. |
| Need a run's full output | `cairn logs <agent-id>`; on disk at `$CAIRN_HOME/workspaces/{agent_id}/.cairn/run.log` (kept for errored agents until retention/reject). |

## Related

- Full CLI reference: [CLI_README](../../../docs/CLI_README.md).
- Thin-client + transport contracts: [SPEC § CLI contract / Transport](../../../docs/SPEC.md).
- Implementations: `src/cairn/cli/cli.py`,
  `src/cairn/orchestrator/transport.py`, `src/cairn/orchestrator/lifecycle.py`.
- Integration tests: `tests/cairn/integration/test_cli_daemon.py`,
  `tests/cairn/integration/test_accept_safety.py`.
