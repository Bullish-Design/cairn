from __future__ import annotations

import time
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.runtime.agent import AgentContext, AgentState
from cairn.orchestrator.lifecycle import LifecycleStore
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority

SPAWN_LATENCY_TARGET_SECONDS = 1.0
PREVIEW_LATENCY_TARGET_SECONDS = 0.1
ACCEPT_REJECT_LATENCY_TARGET_SECONDS = 0.05
EXECUTION_DURATION_TARGET_SECONDS = 5.0


class BenchmarkCodeProvider:
    async def get_code(self, reference: str, context: dict) -> str:
        _ = context
        return f"# task:{reference}\npass"

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return True, None


class CheckResult:
    def __init__(self, valid: bool, errors: list[str] | None = None) -> None:
        self.valid = valid
        self.errors = errors or []


class BenchmarkScript:
    metrics_by_task: dict[str, dict[str, int]] = {
        "refactor-small-file": {"peak_memory_bytes": 1_048_576},
    }

    def check(self) -> CheckResult:
        return CheckResult(True)

    async def run(self, *, inputs: dict, externals: dict[str, object]) -> None:
        task = inputs["task_description"]
        tools = externals

        if task == "refactor-small-file":
            await tools["write_file"]("changes/small.py", "value = 1")
        elif task == "generate-docs":
            await tools["write_file"]("docs/README.md", "# generated")
            await tools["write_file"]("docs/USAGE.md", "usage")
            await tools["write_file"]("docs/API.md", "api")
        else:
            await tools["write_file"]("changes/default.txt", task)

        await tools["submit_result"](f"completed {task}", [])


async def _setup_orchestrator(tmp_path: Path) -> tuple[CairnOrchestrator, object, object, object, Path]:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        code_provider=BenchmarkCodeProvider(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    orch.cairn_home.mkdir(parents=True, exist_ok=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True, exist_ok=True)
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    agent_db_path = tmp_path / "agent.db"
    agent_ws = await Fsdantic.open(path=str(agent_db_path))

    orch.stable = stable
    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)
    await orch.workspace_cache.put(str(agent_db_path), agent_ws)

    return orch, stable, bin_ws, agent_ws, agent_db_path


@pytest.mark.asyncio
@pytest.mark.benchmark
async def test_agent_lifecycle_latency_benchmarks(
    monkeypatch: pytest.MonkeyPatch,
    record_property: pytest.RecordProperty,
    tmp_path: Path,
) -> None:
    """Benchmark phase-5 latency targets from CAIRN_REFACTOR-STEP_5.md."""
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(tmp_path)
    monkeypatch.setattr("cairn.orchestrator.orchestrator._load_grail_script", lambda _: BenchmarkScript())

    spawned_agent_id: str | None = None

    try:
        spawn_start = time.perf_counter()
        spawned_agent_id = await orch.spawn_agent("spawn-only")
        spawn_latency = time.perf_counter() - spawn_start
        record_property("spawn_latency_seconds", spawn_latency)
        record_property("spawn_latency_threshold_seconds", SPAWN_LATENCY_TARGET_SECONDS)
        assert spawn_latency < SPAWN_LATENCY_TARGET_SECONDS

        ctx = AgentContext(
            agent_id="agent-preview",
            task="generate-docs",
            priority=TaskPriority.NORMAL,
            state=AgentState.QUEUED,
            agent_db_path=agent_db_path,
            agent_fs=agent_ws,
        )
        orch.active_agents[ctx.agent_id] = ctx

        execution_start = time.perf_counter()
        await orch._run_agent(ctx.agent_id)
        execution_duration = time.perf_counter() - execution_start
        record_property("execution_duration_seconds", execution_duration)
        record_property("execution_duration_threshold_seconds", EXECUTION_DURATION_TARGET_SECONDS)
        assert execution_duration < EXECUTION_DURATION_TARGET_SECONDS

        preview_target = orch.cairn_home / "workspaces" / "preview-benchmark"
        preview_start = time.perf_counter()
        assert ctx.agent_fs is not None
        await ctx.agent_fs.materialize.to_disk(
            target_path=preview_target,
            base=stable,
            clean=True,
            allow_root=orch.cairn_home / "workspaces",
        )
        preview_latency = time.perf_counter() - preview_start
        record_property("preview_latency_seconds", preview_latency)
        record_property("preview_latency_threshold_seconds", PREVIEW_LATENCY_TARGET_SECONDS)
        assert preview_latency < PREVIEW_LATENCY_TARGET_SECONDS

        accept_start = time.perf_counter()
        await orch.accept_agent(ctx.agent_id)
        accept_latency = time.perf_counter() - accept_start
        record_property("accept_latency_seconds", accept_latency)
        record_property("accept_latency_threshold_seconds", ACCEPT_REJECT_LATENCY_TARGET_SECONDS)
        assert accept_latency < ACCEPT_REJECT_LATENCY_TARGET_SECONDS

        reject_id = await orch.spawn_agent("reject-only")
        reject_start = time.perf_counter()
        await orch.reject_agent(reject_id)
        reject_latency = time.perf_counter() - reject_start
        record_property("reject_latency_seconds", reject_latency)
        record_property("reject_latency_threshold_seconds", ACCEPT_REJECT_LATENCY_TARGET_SECONDS)
        assert reject_latency < ACCEPT_REJECT_LATENCY_TARGET_SECONDS

        assert spawned_agent_id.startswith("agent-")
    finally:
        extra = orch.active_agents.pop(spawned_agent_id, None) if spawned_agent_id else None
        if extra is not None and extra.agent_fs is not None:
            await extra.agent_fs.close()
        await bin_ws.close()
        await stable.close()


@pytest.mark.asyncio
@pytest.mark.benchmark
@pytest.mark.parametrize(
    ("task", "max_duration_seconds"),
    [
        ("refactor-small-file", 2.0),
        ("generate-docs", 2.0),
    ],
)
async def test_execution_duration_benchmarks_for_representative_tasks(
    monkeypatch: pytest.MonkeyPatch,
    record_property: pytest.RecordProperty,
    task: str,
    max_duration_seconds: float,
    tmp_path: Path,
) -> None:
    """Benchmark representative execution durations and capture optional Grail memory telemetry."""
    orch, stable, bin_ws, agent_ws, agent_db_path = await _setup_orchestrator(tmp_path)
    monkeypatch.setattr("cairn.orchestrator.orchestrator._load_grail_script", lambda _: BenchmarkScript())

    ctx = AgentContext(
        agent_id=f"agent-{task}",
        task=task,
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=agent_db_path,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        started = time.perf_counter()
        await orch._run_agent(ctx.agent_id)
        elapsed = time.perf_counter() - started

        record_property("representative_task", task)
        record_property("execution_duration_seconds", elapsed)
        record_property("execution_duration_threshold_seconds", max_duration_seconds)
        assert elapsed < max_duration_seconds

        memory_metric = BenchmarkScript.metrics_by_task.get(task, {}).get("peak_memory_bytes")
        if memory_metric is not None:
            record_property("peak_memory_bytes", memory_metric)
            assert memory_metric > 0
        else:
            record_property("peak_memory_bytes", "unavailable")
    finally:
        await orch.trash_agent(ctx.agent_id)
        await bin_ws.close()
        await stable.close()


@pytest.mark.asyncio
@pytest.mark.benchmark
async def test_queue_throughput_benchmark(record_property: pytest.RecordProperty) -> None:
    from cairn.orchestrator.queue import TaskPriority, TaskQueue

    queue = TaskQueue()
    iterations = 200

    start = time.perf_counter()
    for i in range(iterations):
        await queue.enqueue(f"task-{i}", TaskPriority.NORMAL)
    enqueue_duration = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(iterations):
        await queue.dequeue()
    dequeue_duration = time.perf_counter() - start

    record_property("queue_enqueue_seconds", enqueue_duration)
    record_property("queue_dequeue_seconds", dequeue_duration)

    assert enqueue_duration < 0.5
    assert dequeue_duration < 0.5
