# Cairn Technical Specification

Version: 1.2
Status: Active
Updated: 2026-02-13

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
     - `FileCodeProvider` - loads code from `.pym` files on disk
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

3. **Execution (`grail.load()` and `.pym` files)**
   - Code providers generate or fetch code that is written to `.grail/agents/{agent_id}/task.pym`.
   - Each agent execution uses `grail.load(pym_path)` to create a script object.
   - Pre-flight validation via `script.check()` catches errors before execution.
   - Execution via `script.run(inputs={...}, externals={...})` with external functions.
   - Execution limits (timeout, memory) are enforced by Grail/Monty runtime.

4. **External functions (`create_external_functions`)**
   - External function callables are created per-agent by `create_external_functions(agent_context)`.
   - The returned dict (`read_file`, `write_file`, `list_dir`, `file_exists`, `search_files`, `search_content`, `submit_result`, `log`) is the canonical capability surface.
   - `submit_result(...)` writes review payloads to the agent workspace KV submission record consumed by the orchestrator lifecycle flow.

> **Source-of-truth note:** If runtime behavior in code and this section differ, update this section and the implementing modules together in the same change (`src/cairn/orchestrator/orchestrator.py`, `src/cairn/providers/providers.py`, `src/cairn/runtime/external_functions.py`).

## Data layout contract

```text
$PROJECT_ROOT/.agentfs/
├── stable.db
├── agent-{id}.db
└── bin.db

$PROJECT_ROOT/.grail/
└── agents/
    └── {agent_id}/
        ├── task.pym           # Generated/loaded agent code
        ├── check.json         # Validation results
        └── run.log            # Execution log

$CAIRN_HOME/ (default ~/.cairn)
├── workspaces/
├── previews/
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

## Execution contracts (Grail + Monty)

### .pym File Structure

All executable code is written to `.pym` files with the following structure:

```python
from grail import external, Input

# Inputs
task_description: str = Input("task_description")

# External function stubs
@external
async def read_file(path: str) -> str: ...

@external
async def write_file(path: str, content: str) -> bool: ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool: ...

# Task code
content = await read_file("/src/main.py")
# ... process ...
await submit_result(summary="Done", changed_files=["/src/main.py"])

# Return value
{"status": "complete"}
```

### Sandbox policy

Allowed:
- procedural Python constructs,
- async functions,
- calls to declared `@external` functions.

Disallowed:
- imports (except `from grail import ...`),
- host filesystem/network access except through external functions,
- subprocess execution,
- implicit environment access.

### Required external functions exposed to code

- `read_file(path) -> str`
- `write_file(path, content) -> bool`
- `list_dir(path) -> list[str]`
- `file_exists(path) -> bool`
- `search_files(pattern) -> list[str]`
- `search_content(pattern, path='.') -> list[dict]`
- `submit_result(summary, changed_files) -> bool`
- `log(message) -> None`

### Pre-flight validation

Before execution, `grail.load(pym_path).check()` validates:
- syntax errors,
- undefined @external functions,
- type consistency,
- structural correctness.

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
- validate code via `grail.load().check()` before execution,
- write code to `.grail/agents/{agent_id}/task.pym`,
- execute code via `grail.load().run()` with external functions,
- materialize preview workspace via `workspace.materialize.to_disk()`,
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
- With `FileCodeProvider` (default): `reference` is a path to a `.pym` file
- With `LLMCodeProvider` (--provider llm): `reference` is natural language task description
- With `GitCodeProvider`: `reference` is a git URL with path (e.g., `git://github.com/org/repo:script.pym`)
- With `RegistryCodeProvider`: `reference` is a registry URL (e.g., `registry://org/script-name:version`)

### Signal adapter contract

Signals are an optional transport adapter. When `enable_signal_polling=true`, the orchestrator watches `$CAIRN_HOME/signals/*.json` and routes each file through the same command parser + `submit_command` path used by CLI ingress. When disabled, signal parsing semantics remain identical for manual/explicit `process_signals_once` processing.

## Documentation boundaries

To avoid drift:
- `../README.md`: setup + first commands only.
- `CONCEPT.md`: conceptual model and invariants only.
- `SPEC.md`: runtime details and contracts only.
- `.agents/skills/*`: implementation workflows that link back to these canonical docs.
