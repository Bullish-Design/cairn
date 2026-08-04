---
name: cairn-cli-operations
description: >-
  Operating a Cairn deployment from the command line: starting the daemon
  (cairn up) or running inline (cairn run), submitting work (spawn/queue),
  the review flow (list-agents, status, accept with staleness checks, reject,
  undo, logs), the signal-file transport and lifecycle mirror, exit codes, and
  troubleshooting common failures. Use when running or debugging Cairn via CLI.
license: MIT
metadata:
  subsystem: cli
---

# Cairn CLI Operations

Two CLIs ship with the package: `cairn` (argparse) and `cairn-cli` (Typer,
richer output). Both implement the same **thin-client contract**: the daemon
owns the databases; mutating commands write signal files the daemon picks up,
query commands read the daemon's lifecycle mirror read-only. **No subcommand
constructs an orchestrator.**

Canonical references: [CLI_README](../../../docs/CLI_README.md) (cairn-cli
reference), [SPEC § CLI contract](../../../docs/SPEC.md), and
`src/cairn/cli/cli.py` / `src/cairn/cli/typer_cli.py`.

## Core commands (`cairn`)

| Command | Purpose | Notes |
|---|---|---|
| `cairn up` | Start the daemon (claims `~/.cairn/state/orchestrator.pid`; a second `cairn up` in the same `CAIRN_HOME` is refused) | Long-running; owns all databases |
| `cairn run <task> [--timeout N]` | Run one task inline to completion, no daemon | Refused while a daemon runs; exit 0 if REVIEWING, else 1 |
| `cairn spawn <reference>` | High-priority task (`TaskPriority.HIGH`) | Mutating → signal |
| `cairn queue <reference>` | Normal-priority task (`TaskPriority.NORMAL`) | Mutating → signal |
| `cairn list-agents` | Read the lifecycle mirror | Query |
| `cairn status <agent-id>` | Read the mirror; unknown agent → exit 1, friendly message, no traceback | Query |
| `cairn accept <agent-id> [--timeout N] [--force]` | Accept → signal + poll until settled | Staleness check unless `--force` |
| `cairn reject <agent-id> [--timeout N]` | Reject → signal + poll | Allowed from REVIEWING, QUEUED, ERRORED |
| `cairn undo <agent-id>` | Restore stable to pre-accept state | Needs the undo snapshot (see below) |
| `cairn logs <agent-id>` | Print the sandbox run log | Works for errored agents too |

**Exit codes:** `0` success; `1` unknown agent or accept/reject failed;
`2` mutating command with no daemon running (with guidance to run `cairn up`
or `cairn run`).

Common flags on every command: `--project-root`, `--cairn-home`,
`--provider <file|inline|plugin>`, `--provider-base-path`, plus executor flags
(`--max-execution-time`, `--max-memory-bytes`, `--max-recursion-depth`).

## `cairn-cli` command groups

- **workspace**: `create <name>`, `list`, `info <name>`, `delete <name>
  [--force]` — operates on `.agentfs/*.db`.
- **files**: `list <ws> [--path] [--recursive]`, `read <ws> <path> [--binary]`,
  `write <ws> <path> <content>`, `search <ws> <pattern>`, `tree <ws>
  [--path] [--max-depth]` — all open the workspace read-only except `write`.
- **agent**: `list`, `status <id>`, `spawn <task>`, `queue <task>`,
  `accept <id> [--force]`, `reject <id>`, `undo <id>`, `logs <id>`,
  `run <task> [--timeout]`, `up`.
- **preview**: `changes <agent-id>` (diff vs stable), `file <agent-id>
  <path>` — read-only.

`cairn-cli` inspection commands open workspaces **read-only** (`readonly=True`),
so they never modify the databases they inspect.

## How the plumbing works

1. **Signals (mutations).** The CLI writes
   `$CAIRN_HOME/signals/{type}-{signal_id}.json` atomically (temp name +
   rename, so the watcher never sees a partial file). The daemon watches the
   directory and runs a periodic sweep (1 s backstop) so a signal written
   during startup isn't lost. Processing claims a file by renaming it to
   `*.processing` (atomic — only one observer wins), dispatches, then removes
   it; failures are quarantined to `$CAIRN_HOME/signals/failed/` with an
   `.error.txt` sidecar.
2. **Lifecycle mirror (queries).** The daemon rewrites
   `$CAIRN_HOME/state/lifecycle.json` after every lifecycle mutation. The CLI
   reads only that file — it can't open `bin.db` while the daemon holds it
   (pyturso takes an exclusive file lock even for read-only opens).
3. **Pidfile.** `$CAIRN_HOME/state/orchestrator.pid` guards the daemon; stale
   pidfiles (dead process) are ignored.

## The review flow

```bash
cairn up &                                  # 1. start the daemon
cairn queue scripts/refactor_imports.py     # 2. submit work
cairn list-agents                           # 3. find the agent id + state
cairn status agent-1a2b3c4d                 # 4. inspect (state, error, submission)
cairn logs agent-1a2b3c4d                   #    failed/odd run? read the sandbox log
cairn accept agent-1a2b3c4d                 # 5a. merge overlay into stable
# or
cairn reject agent-1a2b3c4d                 # 5b. discard the overlay
# or, to reverse a (possibly wrong) accept:
cairn undo agent-1a2b3c4d
```

### Accept semantics

- The merge is `MergeStrategy.OVERWRITE` from the agent overlay into stable.
- **Staleness check (default):** if stable changed for any path the agent
  touched since the agent read it (compared via the run record's base hashes),
  accept is refused with `ACCEPT_STALE_BASE` and a message listing the stale
  paths. `--force` bypasses this (you may silently discard the concurrent
  edit — use with care).
- **Tombstones:** deletions made in the sandbox are re-imported as overlay
  tombstones; the accept merge replays them against stable
  (`tombstones_applied`). A file re-created in the overlay after deletion
  overrides its tombstone (the file wins).
- Output reports `files_merged` and `tombstones_applied`.
- **Undo:** before merging, the daemon snapshots stable's content for every
  path the accept will touch into `bin.db` under `undo/{agent_id}/`. `cairn
  undo <agent-id>` restores those files (and deletes files that didn't exist
  before). Snapshots expire on the 7-day retention schedule.

### Reject semantics

Allowed from `REVIEWING`, `QUEUED` (drops the queued entry so the worker never
dequeues a phantom), and `ERRORED`. The overlay and workdir are discarded
(moved to `bin-{agent_id}.db` and removed from `workspaces/`).

### Status claims vs. ground truth

`cairn status` prints `files_written`, `files_deleted`, and `claim_mismatch`.
If the agent's `submit_result(changed_files=...)` disagrees with what the
sandbox actually changed, status prints both lists plus
`! the agent's self-report does not match what it did` on stderr. Trust the
ground-truth lists.

## Reference interpretation

With `FileCodeProvider` (default) `reference` is a **path to a Python script**
(project-relative). With `--provider llm` it's a natural-language description;
`git`/`registry` providers accept their own schemes — see
[cairn-code-providers](../cairn-code-providers/SKILL.md).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `cairn queue` exits 2, "No Cairn daemon is running" | No daemon for this `CAIRN_HOME`. Start `cairn up`, or use `cairn run <task>` inline. |
| `cairn run` refuses with "A daemon is running" | `cairn run` is single-process; use `cairn queue`/`spawn` instead. |
| `cairn up` refuses: "A Cairn daemon is already running" | Pidfile liveness probe found a live process. Check `$CAIRN_HOME/state/orchestrator.pid`. |
| Agent stuck QUEUED and never runs | Worker loop error — check daemon logs; the worker survives per-iteration failures with backoff and is restarted by a supervisor if it exits. |
| Agent ERRORED after daemon restart | Crash recovery: mid-run agents (`GENERATING`/`EXECUTING`/`SUBMITTING`) are failed with "Interrupted by orchestrator restart". Re-queue with `requeue_interrupted=True` setting if desired. |
| `cairn status <id>` → "Unknown agent" | Agent may have been cleaned by retention (7 days) or trashed. Check `cairn list-agents`. |
| `cairn accept` → `accept failed: ...` | The agent's record shows the error (e.g. `ACCEPT_STALE_BASE`). Fix the conflict or pass `--force` knowingly. |
| `cairn undo` → `UNDO_NOT_FOUND` | No snapshot: never accepted, or expired by retention. |
| Signal files accumulating in `signals/failed/` | Read the `.error.txt` sidecar — failed dispatches are quarantined, not deleted, for exactly this reason. |
| Need a run's full output | `cairn logs <agent-id>`; on disk at `$CAIRN_HOME/workspaces/{agent_id}/.cairn/run.log` (kept for errored agents until retention/reject). |

## Related

- Full `cairn-cli` reference: [CLI_README](../../../docs/CLI_README.md).
- Thin-client + signal contracts: [SPEC § CLI contract / Signal adapter](../../../docs/SPEC.md).
- Implementations: `src/cairn/cli/cli.py`, `src/cairn/cli/typer_cli.py`,
  `src/cairn/orchestrator/signals.py`, `src/cairn/orchestrator/lifecycle.py`.
- Integration tests: `tests/cairn/integration/test_cli_daemon.py`,
  `tests/cairn/integration/test_accept_safety.py`.
