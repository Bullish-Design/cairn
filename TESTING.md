# Testing Guide

Run all commands from the repository root.

## Standard test runs

```bash
# Full suite
uv run pytest

# With coverage summary
uv run pytest --cov=src/cairn --cov-report=term-missing
```

## Targeted suites in this repository

```bash
# Core orchestrator + workspace flow
uv run pytest tests/cairn/test_orchestrator.py tests/cairn/test_workspace.py

# Agent lifecycle and tools
uv run pytest tests/cairn/test_lifecycle.py tests/cairn/test_agent_tools.py tests/cairn/test_watcher.py

# Performance-marked tests
uv run pytest -m benchmark tests/cairn/test_performance.py
```

## Test file inventory

Current test modules under `tests/cairn/`:

- `test_agent_tools.py`
- `test_lifecycle.py`
- `test_orchestrator.py`
- `test_performance.py`
- `test_plugin_providers.py`
- `test_providers.py`
- `test_watcher.py`
- `test_workspace.py`

## Notes

- Pytest configuration lives in `pyproject.toml` (`[tool.pytest.ini_options]`).
- Benchmark tests are marked with `@pytest.mark.benchmark`.
- There are no repository-local `test-*` shell helpers in `devenv.nix`; use `uv run pytest ...` commands above.

> **Source-of-truth note:** Keep this file aligned with the actual files in `tests/cairn/` and runnable commands in this repository; remove stale commands when test layout changes.
