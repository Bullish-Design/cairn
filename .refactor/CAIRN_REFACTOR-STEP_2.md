# Cairn Refactoring Plan: Phase 2

## Executive Summary

This document outlines Phase 2 of refactoring of Cairn from its current implementation to a simpler, more powerful architecture built on top of two foundational libraries:

- **fsdantic**: Workspace-first, async Python library providing type-safe, Pydantic-based interface for AgentFS
- **grail**: Pydantic-native wrapper around Monty for executing untrusted Python code in sandboxed environments

---

## Detailed Refactoring Plan


### Phase 2: Leverage Fsdantic Workspace Abstraction

**Goal:** Replace direct agentfs-sdk usage with fsdantic Workspace

#### 2.1 Replace Lifecycle Store with TypedKVRepository

**Current:** `lifecycle.py` uses custom `TypedKVRepository` from `kv_models.py`

**New:** Use fsdantic's built-in `TypedKVRepository`

```python
# lifecycle.py - refactored
from fsdantic import Workspace, TypedKVRepository
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class LifecycleRecord(BaseModel):
    agent_id: str
    task: str
    priority: int
    state: str
    created_at: datetime
    updated_at: datetime
    db_path: Optional[str] = None
    submission: Optional[dict] = None
    error: Optional[str] = None

class LifecycleStore:
    def __init__(self, workspace: Workspace):
        self.repo: TypedKVRepository[LifecycleRecord] = workspace.kv.typed(
            model=LifecycleRecord,
            prefix="lifecycle:"
        )

    async def save(self, record: LifecycleRecord) -> None:
        await self.repo.put(record.agent_id, record)

    async def get(self, agent_id: str) -> Optional[LifecycleRecord]:
        return await self.repo.get(agent_id)

    async def list_all(self) -> list[LifecycleRecord]:
        return await self.repo.list()

    async def delete(self, agent_id: str) -> None:
        await self.repo.delete(agent_id)
```

**Benefits:**
- No custom KV implementation needed
- Type-safe with Pydantic validation
- Automatic serialization/deserialization
- Built-in batch operations
- Query support if needed

#### 2.2 Use Workspace for Overlay Operations

**Current:** Manual overlay creation and merging via agentfs-sdk

**New:** Use `workspace.overlay` operations

```python
# In orchestrator.py
async def create_agent_overlay(
    agent_id: str,
    stable_workspace: Workspace
) -> Workspace:
    # Create overlay workspace
    overlay = await stable_workspace.overlay.create(
        overlay_id=agent_id,
        description=f"Agent {agent_id} workspace"
    )
    return overlay

async def merge_agent_changes(
    agent_id: str,
    stable_workspace: Workspace
) -> None:
    # Merge overlay into stable
    await stable_workspace.overlay.merge(
        overlay_id=agent_id,
        conflict_strategy="overlay_wins"  # Agent changes take precedence
    )

async def discard_agent_changes(
    agent_id: str,
    stable_workspace: Workspace
) -> None:
    # Delete overlay
    await stable_workspace.overlay.delete(overlay_id=agent_id)
```

**Benefits:**
- Clean overlay lifecycle management
- Built-in conflict resolution strategies
- Change tracking and diff support
- Less error-prone than manual operations

#### 2.3 Use Workspace Materialization

**Current:** Custom `workspace.py` with manual file copying

**New:** Use `workspace.materialize` for preview

```python
# Replace workspace.py entirely
async def materialize_agent_workspace(
    agent_id: str,
    overlay_workspace: Workspace,
    preview_dir: Path
) -> None:
    # Materialize overlay to disk for preview
    await overlay_workspace.materialize.to_disk(
        target_path=preview_dir,
        include_patterns=["**/*"],
        exclude_patterns=[".git/**", "__pycache__/**"]
    )

async def get_agent_diff(
    agent_id: str,
    overlay_workspace: Workspace,
    stable_workspace: Workspace
) -> str:
    # Get unified diff between overlay and stable
    diff = await overlay_workspace.materialize.diff(
        other=stable_workspace,
        format="unified"
    )
    return diff
```

**Benefits:**
- Robust file copying with error handling
- Built-in diff generation
- Pattern-based filtering
- Less custom code

#### 2.4 Update File Watcher

**Current:** `watcher.py` syncs to stable.db via agentfs-sdk

**New:** Use workspace.files for syncing

```python
# watcher.py - refactored
from fsdantic import Workspace
from watchfiles import awatch

class FileWatcher:
    def __init__(self, workspace: Workspace, project_root: Path):
        self.workspace = workspace
        self.project_root = project_root

    async def watch(self):
        async for changes in awatch(
            self.project_root,
            ignore_paths=[".agentfs", ".git", "__pycache__", "node_modules"]
        ):
            for change_type, path in changes:
                rel_path = Path(path).relative_to(self.project_root)

                if change_type == "added" or change_type == "modified":
                    # Read from disk and write to workspace
                    content = Path(path).read_text()
                    await self.workspace.files.write(str(rel_path), content)

                elif change_type == "deleted":
                    # Delete from workspace
                    await self.workspace.files.delete(str(rel_path))
```

**Benefits:**
- Cleaner integration with workspace abstraction
- Type-safe file operations
- Better error handling
