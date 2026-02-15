# Cairn Refactoring Plan: Phase 3

## Executive Summary

This document outlines Phase 3 of refactoring of Cairn from its current implementation to a simpler, more powerful architecture built on top of two foundational libraries:

- **fsdantic**: Workspace-first, async Python library providing type-safe, Pydantic-based interface for AgentFS
- **grail**: Pydantic-native wrapper around Monty for executing untrusted Python code in sandboxed environments

---

### Phase 3: Simplify Orchestrator

**Goal:** Streamline orchestrator using new abstractions

#### 3.1 Refactor Orchestrator Structure

**Current:** Large `orchestrator.py` with manual state management

**New:** Simplified using workspace and grail

```python
# orchestrator.py - refactored structure
from fsdantic import Workspace
from grail import MontyContext
from .agent_tools import create_agent_tools
from .lifecycle import LifecycleStore, LifecycleRecord
from .queue import TaskQueue
from .code_generator import generate_agent_code

class Orchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stable_workspace: Workspace = None
        self.lifecycle_store: LifecycleStore = None
        self.queue = TaskQueue()
        self.active_agents: dict[str, AgentContext] = {}
        self.semaphore = asyncio.Semaphore(settings.AGENT_MAX_CONCURRENT)

    async def start(self):
        # Initialize stable workspace
        self.stable_workspace = await Workspace.open(
            db_path=self.settings.state_dir / "stable.db"
        )

        # Initialize lifecycle store
        self.lifecycle_store = LifecycleStore(self.stable_workspace)

        # Recover from crash
        await self.recover()

        # Start workers
        await asyncio.gather(
            self.worker_loop(),
            self.command_loop(),
            self.file_watcher_loop()
        )

    async def spawn_agent(self, task: str, priority: int):
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"

        # Create lifecycle record
        record = LifecycleRecord(
            agent_id=agent_id,
            task=task,
            priority=priority,
            state="QUEUED",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        await self.lifecycle_store.save(record)

        # Add to queue
        await self.queue.enqueue(agent_id, task, priority)

        return agent_id

    async def run_agent(self, agent_id: str):
        async with self.semaphore:
            # Get lifecycle record
            record = await self.lifecycle_store.get(agent_id)

            # Create overlay workspace
            overlay = await self.stable_workspace.overlay.create(agent_id)

            # Update state: GENERATING
            await self.update_state(agent_id, "GENERATING")

            # Generate code
            code = await generate_agent_code(record.task, self.settings)

            # Update state: EXECUTING
            await self.update_state(agent_id, "EXECUTING")

            # Execute with grail
            result = await execute_agent_code(
                agent_id=agent_id,
                code=code,
                workspace=overlay,
                settings=self.settings
            )

            if result.success:
                # Update state: REVIEWING
                await self.update_state(agent_id, "REVIEWING")

                # Materialize for preview
                preview_dir = self.settings.state_dir / "workspaces" / agent_id
                await overlay.materialize.to_disk(preview_dir)

                # Store overlay reference
                record.overlay_id = agent_id
                await self.lifecycle_store.save(record)
            else:
                # Update state: ERRORED
                record.error = result.error
                await self.update_state(agent_id, "ERRORED")

    async def accept_agent(self, agent_id: str):
        # Merge overlay into stable
        await self.stable_workspace.overlay.merge(
            overlay_id=agent_id,
            conflict_strategy="overlay_wins"
        )

        # Update state
        await self.update_state(agent_id, "ACCEPTED")

        # Cleanup
        await self.cleanup_agent(agent_id)

    async def reject_agent(self, agent_id: str):
        # Discard overlay
        await self.stable_workspace.overlay.delete(agent_id)

        # Update state
        await self.update_state(agent_id, "REJECTED")

        # Cleanup
        await self.cleanup_agent(agent_id)
```

**Benefits:**
- Cleaner separation of concerns
- Less boilerplate
- Type-safe throughout
- Easier to test

#### 3.2 Remove Redundant Components

**Files to Remove:**
- `executor.py` → Replaced by grail MontyContext usage
- `external_functions.py` → Replaced by `agent_tools.py`
- `kv_models.py` → Replaced by fsdantic TypedKVRepository
- `workspace.py` → Replaced by workspace.materialize

**Files to Significantly Simplify:**
- `orchestrator.py` → Use workspace and grail abstractions
- `lifecycle.py` → Use TypedKVRepository
- `watcher.py` → Use workspace.files

**Files with Minimal Changes:**
- `queue.py` → Keep as is
- `commands.py` → Keep as is
- `signals.py` → Keep as is
- `cli.py` → Minor updates for new orchestrator API
- `settings.py` → Minor updates for new configuration
