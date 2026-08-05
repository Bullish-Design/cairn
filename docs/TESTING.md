# Testing Guide

Run all commands from the repository root. The full validation gate is
`devenv test` (see `cairn-contribution` skill / `.github/workflows/ci.yml`):

```bash
devenv test        # uv lock --check + ruff check/format + ty check + pytest (coverage floor)
```

## Standard test runs

```bash
# Full suite (benchmarks deselected by default)
devenv shell -- bash -c 'export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH" && pytest -q'

# With coverage summary
pytest -q --cov=cairn --cov-report=term-missing

# Benchmarks (explicitly)
pytest -m benchmark            # environment-tolerant thresholds
CAIRN_STRICT_BENCHMARKS=1 pytest -m benchmark   # real targets
```

## Targeted suites in this repository

```bash
# Repository snapshot + disposable workspace machinery (canonical tree)
pytest tests/cairn/test_repo.py tests/cairn/test_driver.py

# Sandbox executor (unit + real-bwrap integration)
pytest tests/cairn/test_sandbox_executor.py

# Orchestrator lifecycle + accept/undo safety
pytest tests/cairn/test_orchestrator.py tests/cairn/integration/test_accept_safety.py

# Transport (Unix socket) + daemon ownership
pytest tests/cairn/test_transport.py tests/cairn/test_daemon.py

# End-to-end CLI ↔ daemon
pytest tests/cairn/test_cli.py tests/cairn/integration/test_cli_daemon.py

# Adversarial sandbox boundary + resource limits (real bwrap)
pytest tests/cairn/integration/test_sandbox_boundary.py tests/cairn/integration/test_resource_limits.py

# Crash recovery, concurrency, failure injection
pytest tests/cairn/integration/test_crash_recovery.py tests/cairn/integration/test_concurrency.py \
       tests/cairn/integration/test_failure_injection.py

# Provider plugins (git/registry extensions)
pytest tests/cairn/test_plugin_providers.py

# Packaging smoke (builds sdist/wheel, installs into clean venvs)
bash scripts/smoke-test-dist.sh
```

## Test file inventory

Current test modules under `tests/cairn/` (unit tests unless noted):

- `test_accept_safety.py` (integration) — fail-closed accept revalidation,
  undo staleness, journal rollback
- `test_cli.py` — single `cairn` CLI: mirrors, managed-name refusal, name
  validation
- `test_cli_daemon.py` (integration) — thin-client over the socket transport
- `test_concurrency.py` (integration) — parallel agent flows
- `test_crash_recovery.py` (integration) — restart recovery, interrupted
  accepts, in-flight transport commands
- `test_daemon.py` — socket ownership, pidfile as informational
- `test_driver.py` — iterative driver, WorkspaceCapability confinement,
  ProjectView read-only provider view
- `test_e2e_workflows.py` (integration) — spawn → review → accept/reject
- `test_failure_injection.py` (integration)
- `test_fsdantic_features.py` — fsdantic features consumed by Cairn
- `test_integration_lock.py` — project integration lock exclusivity
- `test_lifecycle.py` — LifecycleStore, recovery, retention
- `test_materialize_live_fixture.py` — byte-for-byte workspace
  materialization from the live fixture tree
- `test_orchestrator.py` / `test_orchestrator_phases.py` — lifecycle phases
- `test_packaging.py` — sdist/wheel build configuration
- `test_performance.py` — benchmarks (deselected by default)
- `test_plugin_providers.py` — git/registry provider policies
- `test_providers.py` — built-in providers
- `test_repo.py` — gitignore, no-follow snapshots, materialization fidelity
- `test_resource_limits.py` (integration) — memory/process caps
- `test_sandbox_boundary.py` (integration) — adversarial sandbox cases
- `test_sandbox_executor.py` — executor + real-bwrap integration
- `test_signal_events.py` — removed with the signal transport (see
  `test_transport.py`)
- `test_state.py` — AgentStateManager
- `test_transport.py` — socket request/response, idempotent dispatch
- `test_workspace.py` / `test_workspace_cache.py` — workspace manager/cache
- `test_workspace_api.py` — inspection APIs

## Notes

- Async everywhere: `asyncio_mode = "auto"` in `pyproject.toml`.
- Markers: `integration`, `slow`, `benchmark` (`--strict-markers`).
- The devenv gate fails loudly if the sandbox runtime env vars are missing so
  the real-bwrap tests cannot silently skip.
- Pytest configuration lives in `pyproject.toml` (`[tool.pytest.ini_options]`),
  including the 75% coverage floor.

> **Source-of-truth note:** Keep this file aligned with the actual files in
> `tests/cairn/` and runnable commands in this repository; remove stale
> commands when test layout changes.
