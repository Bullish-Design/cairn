# Cairn Refactoring Plan: Phase 4

## Executive Summary

This document outlines Phase 4 of refactoring of Cairn from its current implementation to a simpler, more powerful architecture built on top of two foundational libraries:

- **fsdantic**: Workspace-first, async Python library providing type-safe, Pydantic-based interface for AgentFS
- **grail**: Pydantic-native wrapper around Monty for executing untrusted Python code in sandboxed environments

---

### Phase 4: Add Advanced Features from Grail

**Goal:** Leverage grail's advanced capabilities

#### 4.1 Add Observability

**New:** Use grail's built-in metrics and logging

```python
# Add metrics collection
from grail.observability import MetricsCollector, LogCollector

class Orchestrator:
    def __init__(self, settings: Settings):
        # ... existing init
        self.metrics = MetricsCollector()
        self.logs = LogCollector()

    async def run_agent(self, agent_id: str):
        # ... existing code

        # Collect execution metrics
        execution_metrics = result.metrics
        await self.metrics.record(
            agent_id=agent_id,
            duration=execution_metrics.duration_seconds,
            tool_calls=execution_metrics.tool_call_count,
            memory_peak=execution_metrics.peak_memory_bytes
        )

        # Store logs
        await self.logs.save(agent_id, result.logs)

    async def get_agent_metrics(self, agent_id: str) -> dict:
        return await self.metrics.get(agent_id)

    async def get_agent_logs(self, agent_id: str) -> list[str]:
        return await self.logs.get(agent_id)
```

**CLI Updates:**
```bash
# New commands
cairn logs agent-abc123
cairn metrics agent-abc123
```

#### 4.2 Add Resumable Execution (Future)

**New:** Support for long-running agents with human-in-the-loop

```python
# For future: agents that pause for human input
from grail import SnapshotManager

async def run_resumable_agent(self, agent_id: str):
    snapshot_mgr = SnapshotManager(storage_path=self.settings.state_dir / "snapshots")

    # Check for existing snapshot
    if await snapshot_mgr.exists(agent_id):
        # Resume from snapshot
        result = await ctx.resume(agent_id)
    else:
        # Start fresh
        result = await ctx.execute(input_data)

        # If agent requests human input, save snapshot
        if result.status == "paused":
            await snapshot_mgr.save(agent_id, result.snapshot)
```

**Benefits for Future:**
- Agents can pause for human clarification
- Long-running agents can be resumed after crashes
- Better support for complex multi-step tasks

#### 4.3 Add Retry Policies

**New:** Use grail's retry capabilities

```python
from grail import RetryPolicy, RetryStrategy

# Configure retry for transient errors
retry_policy = RetryPolicy(
    strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
    max_attempts=3,
    initial_delay=1.0,
    max_delay=10.0,
    retryable_errors=["NetworkError", "TimeoutError"]
)

ctx = MontyContext(
    tool_registry=tools,
    resource_policy=policy,
    retry_policy=retry_policy
)
```

**Benefits:**
- Automatic retry for network errors
- Configurable backoff strategies
- Better resilience
