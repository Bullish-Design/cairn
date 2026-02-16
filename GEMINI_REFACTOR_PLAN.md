Based on the analysis of the `fsdantic` and `grail` libraries, here is the `CAIRN_REFACTOR.md` document outlining the architectural rewrite of the Cairn library.

```markdown
# Cairn Refactor Strategy

**Status:** Proposed
**Target:** Cairn v2.0
**Dependencies:** `fsdantic >= 0.3.0`, `grail >= 2.0.0`

## 1. Executive Summary

This document outlines the plan to rewrite Cairn as a lightweight orchestration layer that leverages the modernized `fsdantic` and `grail` libraries. 

**The Core Shift:**
* **Old Cairn:** A heavy orchestrator managing complex runtime policies, custom filesystem hooks, and opaque agent loops.
* **New Cairn:** A thin "wiring" application that connects an `fsdantic` Workspace (State/Storage) to a `grail` Script (Logic/Execution).

The goal is to reduce Cairn's codebase by ~60% while increasing reliability, inspectability, and developer ergonomics.

---

## 2. Architecture Overview

### 2.1 The "Two-Workspace" Data Model
Cairn will strictly enforce the distinction between "Stable" and "Agent" via `fsdantic` workspaces.

1.  **Stable Workspace (`stable`):** The source of truth.
2.  **Agent Workspace (`agent-{id}`):** An isolated scratchpad.

We will utilize `fsdantic.FileOperations` with its native **fallthrough** capability to handle the overlay logic, removing the need for custom filesystem implementations.

```python
# Conceptual wiring in Orchestrator
async with Fsdantic.open("stable") as stable, Fsdantic.open(f"agent-{id}") as agent:
    # Operations automatically read from agent -> fallback to stable
    # Writes always go to agent
    ops = FileOperations(agent_fs=agent, base_fs=stable)

```

### 2.2 The "Agent Kernel" (.pym)

Instead of embedding the agent loop in Python code or hardcoded strings, Cairn will ship with a canonical **`.pym`** file (the "Kernel") that defines the agent's runtime loop.

* **Transparency:** The agent's logic (e.g., ReAct loop, tool usage patterns) is a readable Python file.
* **Type Safety:** Tools are declared as `@external` functions with full type signatures.
* **Validation:** We run `grail check` on the agent kernel at startup.

### 2.3 State Management

We will abandon ad-hoc JSON state files in favor of `fsdantic`'s **Typed Repository Pattern**.

* **Lifecycle:** Stored in `bin.db` via `TypedKVRepository[AgentState]`.
* **Queue:** Managed via strictly typed repository operations.

---

## 3. Implementation Plan

### Phase 1: The Data Layer (`fsdantic` Migration)

**Goal:** Remove all direct `agentfs-sdk` usage and legacy `fsdantic` calls.

1. **Repository Replacement:**
* Create `src/cairn/state.py`.
* Define `AgentState` model inheriting from `VersionedKVRecord`.
* Implement `AgentRepository` wrapping `TypedKVRepository[AgentState]`.
* *Benefit:* Automatic serialization, timestamps, and type safety for agent metadata.


2. **Filesystem & Overlay:**
* Delete `cairn.storage.overlay` and custom merge logic.
* Adopt `fsdantic.FileOperations` for the read/write/fallthrough logic.
* Adopt `fsdantic.OverlayOperations` for the `accept` (merge) and `reset` logic.
* Adopt `fsdantic.Materializer` for the `preview` command.



### Phase 2: The Execution Layer (`grail` Migration)

**Goal:** Remove `MontyContext`, policies, and custom tool registries.

1. **The Agent Kernel (`src/cairn/kernel/standard_agent.pym`):**
* Create a `.pym` file that implements the standard agent loop.
* Declare inputs: `task: str`, `context: dict`.
* Declare externals: `read_file`, `write_file`, `ls`, `ask_llm`.


2. **Grail Wiring:**
* Replace `MontyContext` class with `grail.load()`.
* Map `Cairn` host functions to the `.pym` externals.
* *Critical:* The `read_file` implementation provided to Grail must wrap `FileOperations(agent, stable).read_file`.


3. **Resource Limits:**
* Replace complex policy objects with `grail.DEFAULT` or `grail.STRICT` presets.
* Allow users to override limits via a simple dict in `cairn.config`.



### Phase 3: The Orchestrator

**Goal:** Simplify the CLI and lifecycle management.

1. **Refactor `spawn`:**
* Load `AgentRepository`.
* Create new `AgentState` (QUEUED).
* Initialize `agent-{id}` workspace via `Fsdantic.open`.


2. **Refactor `run/worker`:**
* Load `grail.load("standard_agent.pym")`.
* Inject dependencies (Filesystem ops, LLM client).
* `await script.run(...)`.
* Update `AgentState` on completion.


3. **Refactor `accept`:**
* `workspace.overlay.merge(source=agent, target=stable)`.
* Update state to `ACCEPTED`.



---

## 4. Specific Refactoring Opportunities

### 4.1 Remove "Skills" Abstraction

**Current State:** "Skills" are loose collections of prompts/configs.
**New State:** A "Skill" is simply a **`.pym` file**.

* If a user wants a custom agent (e.g., "QA Agent"), they provide a `qa.pym` file.
* Cairn just loads it via `grail.load()`.
* This creates a powerful plugin architecture: "Cairn is a runtime for `.pym` agents."

### 4.2 Simplify Tooling

**Current State:** `ToolRegistry` with dynamic discovery.
**New State:** A plain dictionary passed to `script.run(externals={...})`.

* The `standard_agent.pym` defines exactly what tools it needs.
* If a user provides a custom `.pym` with extra externals, Cairn can error (or support plugin externals) at load time.

### 4.3 Eliminate Custom Observability

**Current State:** Complex logging/metrics wrappers around Monty.
**New State:**

* Use `grail`'s built-in `.grail/<id>/run.log` for execution logs.
* Use `fsdantic`'s `ToolCall` model for structured audit logs if needed (stored in KV).

---

## 5. Migration Checklist

* [ ] **Define Models:** `AgentState` (pydantic)
* [ ] **Create Kernel:** `src/cairn/kernel/default.pym`
* [ ] **Wiring:** Implement `CairnHost` class that maps `fsdantic` ops to `grail` externals.
* [ ] **CLI:** Rewrite `typer` commands to use `AgentRepository`.
* [ ] **Tests:** Rewrite tests to use `fsdantic`'s in-memory mode and `grail`'s mock externals.
* [ ] **Cleanup:** Delete `cairn/agent_tools.py`, `cairn/policies`, `cairn/legacy`.

## 6. Example: The New `run_agent` Function

```python
async def run_agent(agent_id: str, task: str):
    # 1. Open Workspaces
    async with Fsdantic.open("stable") as stable, Fsdantic.open(f"agent-{agent_id}") as agent:
        
        # 2. Setup Operations (Fallthrough logic)
        fs_ops = FileOperations(agent, base_fs=stable)
        
        # 3. Load Kernel (The Agent Logic)
        script = grail.load(
            "src/cairn/kernel/default.pym", 
            limits=grail.DEFAULT
        )
        
        # 4. Define Externals (The Capabilities)
        externals = {
            "read_file": fs_ops.read_file,
            "write_file": fs_ops.write_file,
            "list_dir": fs_ops.list_dir,
            "ask_llm": llm_client.chat,  # Standard LLM client
        }
        
        # 5. Execute
        try:
            result = await script.run(
                inputs={"task": task}, 
                externals=externals
            )
            # Handle result (commit to KV, etc)
        except grail.ExecutionError as e:
            # Log error state

```

```

```