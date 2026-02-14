# Cairn Technical Specification

Version: 1.2
Status: Active
Updated: 2026-02-13

## Canonical scope of this document

`SPEC.md` is the single source of truth for:
- current runtime architecture,
- filesystem/runtime contracts,
- orchestrator state and CLI behavior.

For philosophy and constraints, see [CONCEPT.md](CONCEPT.md). For install/quickstart, see [README.md](README.md).

## Runtime architecture

Cairn has three layers:

1. **Storage**: AgentFS overlays backed by SQLite (`stable.db`, `agent-*.db`, `bin.db`).
2. **Execution**: Monty sandbox that runs generated agent code with explicit external functions.
3. **Orchestration**: Python process with one worker loop that schedules queueing, execution, review state, and accept/reject.

## Data layout contract

```text
$PROJECT_ROOT/.agentfs/
├── stable.db
├── agent-{id}.db
└── bin.db

$CAIRN_HOME/ (default ~/.cairn)
├── workspaces/
├── previews/
├── signals/
└── state/
```

## Storage contracts (AgentFS)

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

## Execution contracts (Monty)

### Sandbox policy

Allowed:
- procedural Python constructs,
- async functions,
- calls to declared external functions.

Disallowed:
- imports,
- host filesystem/network access except through external functions,
- subprocess execution,
- implicit environment access.

### Required external functions exposed to agents

- `read_file(path)`
- `write_file(path, content)`
- `list_dir(path)`
- `file_exists(path)`
- `search_files(pattern)`
- `search_content(pattern, path='.')`
- `ask_llm(prompt, context='')`
- `submit_result(summary, changed_files)`
- `log(message)`

## Orchestrator contracts

### Agent lifecycle

`QUEUED -> SPAWNING -> GENERATING -> EXECUTING -> SUBMITTING -> REVIEWING -> (ACCEPTED | REJECTED | ERRORED)`

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
- enqueue per-agent overlays into a priority queue,
- run a long-lived worker loop that acquires an `asyncio.Semaphore(max_concurrent_agents)` slot before starting each agent,
- release the semaphore slot in one completion `finally` path,
- generate/execute agent code,
- materialize preview workspace,
- persist lifecycle metadata to canonical KV store on every state transition,
- persist queue stats snapshot under `$CAIRN_HOME/state/` (stats only, not agent metadata).

### CLI contract (current)

CLI subcommands are a transport adapter: each invocation parses into a normalized `CairnCommand` and calls orchestrator `submit_command`.

- `cairn up`
- `cairn spawn <task>`
- `cairn queue <task>`
- `cairn list-agents`
- `cairn status <agent-id>`
- `cairn accept <agent-id>`
- `cairn reject <agent-id>`

### Signal adapter contract

Signals are an optional transport adapter. When `enable_signal_polling=true`, the orchestrator watches `$CAIRN_HOME/signals/*.json` and routes each file through the same command parser + `submit_command` path used by CLI ingress. When disabled, signal parsing semantics remain identical for manual/explicit `process_signals_once` processing.

## Documentation boundaries

To avoid drift:
- `README.md`: setup + first commands only.
- `CONCEPT.md`: conceptual model and invariants only.
- `SPEC.md`: runtime details and contracts only.
- `.agent/skills/*`: implementation workflows that link back to these canonical docs.
