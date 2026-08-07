"""Real-executor benchmarks for the execution hot path.

Unlike test_performance.py (which stubs the executor to isolate orchestrator
latency), these run the actual BwrapExecutor so capture/materialize/sandbox
costs are visible. Skipped when bwrap is unavailable.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from cairn.runtime import repo
from cairn.runtime.sandbox.sandbox import BwrapExecutor
from cairn.runtime.settings import ExecutorSettings

pytestmark = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")

TRIVIAL_TASK = "write_file('out.txt', 'hi')\nsubmit_result('done', ['out.txt'])\n"


def _make_project(root: Path, n_files: int, *, junk_dirs: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        (root / f"mod{i:04d}.py").write_text(f"x = {i}\n", encoding="utf-8")
    if junk_dirs:
        # Excluded-by-name subtrees: the pruning win only shows up with these,
        # which is exactly what a real repo has (.git, .venv, caches).
        for junk in (".git", ".venv", "node_modules"):
            d = root / junk
            d.mkdir(exist_ok=True)
            for i in range(n_files * 3):
                (d / f"blob{i:05d}").write_text("junk", encoding="utf-8")
    return root


@pytest.mark.benchmark
@pytest.mark.parametrize("n_files", [200, 2000])
def test_phase_breakdown(record_property: pytest.RecordProperty, tmp_path: Path, n_files: int) -> None:
    """Record per-phase cost of the real execution path."""
    project = _make_project(tmp_path / "project", n_files, junk_dirs=True)
    workdir = tmp_path / "ws"

    t = time.perf_counter()
    base = repo.capture_manifest(project)
    t_capture = time.perf_counter() - t

    shutil.rmtree(workdir, ignore_errors=True)
    t = time.perf_counter()
    repo.materialize_workspace(project, workdir)
    t_materialize = time.perf_counter() - t

    t = time.perf_counter()
    current = repo.capture_manifest(workdir)
    t_capture_ws = time.perf_counter() - t
    t = time.perf_counter()
    repo.diff_manifests(base, current)
    t_diff = time.perf_counter() - t

    for name, value in [
        ("capture_base_seconds", t_capture),
        ("materialize_seconds", t_materialize),
        ("capture_workspace_seconds", t_capture_ws),
        ("diff_seconds", t_diff),
        ("tree_work_seconds", t_capture + t_materialize + t_capture_ws + t_diff),
        ("n_files", n_files),
    ]:
        record_property(name, value)


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_end_to_end_overhead(record_property: pytest.RecordProperty, tmp_path: Path) -> None:
    """Total overhead of one real sandboxed task over a 200-file project."""
    project = _make_project(tmp_path / "project", 200, junk_dirs=True)
    workdir = tmp_path / "ws"
    settings = ExecutorSettings()

    durations = []
    for _ in range(3):
        shutil.rmtree(workdir, ignore_errors=True)
        executor = BwrapExecutor(agent_id="bench", workdir=workdir, project_root=project, settings=settings)
        t = time.perf_counter()
        result = await executor.run(code=TRIVIAL_TASK, task="bench")
        durations.append(time.perf_counter() - t)
        assert result.exit_code == 0
        assert result.changes["written"] == ["out.txt"]

    record_property("end_to_end_best_seconds", min(durations))
    record_property("end_to_end_median_seconds", sorted(durations)[1])
