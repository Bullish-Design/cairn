# Testing Guide

## Running Tests from Repository Root

The Cairn repository provides comprehensive test coverage for the orchestrator, workspace, and Neovim plugin components.

### Quick Start

```bash
# Run all tests
pytest

# Run unit tests only
pytest tests/cairn/test_agent.py tests/cairn/test_queue.py

# Run with coverage
pytest --cov=src/cairn --cov-report=term-missing

# Run fast tests (skip slow & benchmarks)
pytest -m "not slow and not benchmark"

# Run benchmark suite only
pytest -m benchmark tests/cairn/test_performance.py
```

### Benchmark Workflow (Phase 5)

Phase 5 performance benchmarks live in `tests/cairn/test_performance.py` and encode
the refactor targets from `.refactor/CAIRN_REFACTOR-STEP_5.md`:

- agent spawn latency: `<1s`
- preview materialization latency: `<100ms`
- accept/reject latency: `<50ms`
- representative execution duration benchmarks
- optional per-agent memory telemetry metadata (when Grail metrics are available)

Run from repository root:

```bash
# Run only performance benchmarks
pytest -m benchmark tests/cairn/test_performance.py

# Include benchmarks with the whole suite
pytest -m "benchmark or not benchmark"
```

### Test Scripts (in devenv shell)

If you're using the devenv shell, convenient scripts are available:

```bash
# Enter devenv shell
devenv shell

# Run all tests
test

# Run unit tests only
test-unit

# Run integration tests
test-integration

# Run performance benchmarks
test-performance

# Run property-based tests
test-property

# Run tests with full coverage report
test-cov

# Run fast tests (skip slow & benchmarks)
test-fast
```

### Test Categories

Tests are organized into several categories:

1. **Unit Tests** (`tests/cairn/test_*.py`)
   - Agent state models and lifecycle
   - Queue operations and priority handling
   - Command parsing and validation
   - 100% coverage target for core modules

2. **Integration Tests** (`tests/cairn/test_orchestrator.py`, etc.)
   - Full orchestrator lifecycle
   - Workspace materialization
   - Signal file processing
   - AgentFS overlay integration

3. **Performance Tests**
   - Agent spawn time (<1s target)
   - Preview materialization (<100ms target)
   - File sync operations (<10ms target)
   - Marked with `@pytest.mark.benchmark`

4. **E2E Tests** (`tests/cairn/test_e2e_smoke.py`)
   - Full spawn → reviewing → accept/reject workflow
   - Real AgentFS database operations
   - Command dispatch and state transitions

### Test Markers

Tests can be filtered by markers:

```bash
# Run only benchmark tests
pytest -m benchmark

# Skip slow tests
pytest -m "not slow"

# Run only integration tests
pytest tests/cairn/test_integration.py
```

### Coverage Reports

Coverage reports are generated in multiple formats:

- **Terminal**: Shows missing lines inline
- **HTML**: Browse detailed report at `htmlcov/index.html`
- **XML**: For CI/CD integration at `coverage.xml`

### Configuration

Test configuration is defined in:

- `pyproject.toml` - pytest configuration
- `devenv.nix` - Test scripts for devenv shell (if using Nix)

### Troubleshooting

**Import errors**: Make sure dependencies are synced
```bash
uv sync --all-extras
```

**Module not found**: Ensure you're in the repository root
```bash
cd /path/to/cairn
pytest
```

**Permission errors**: Some tests create temporary files
```bash
# Clear test cache
rm -rf .pytest_cache
```

### CI/CD Integration

For continuous integration, use:

```bash
# Install dependencies
uv sync

# Run full test suite with coverage
pytest \
  --cov=src/cairn \
  --cov-report=xml \
  --cov-report=term-missing \
  --junitxml=junit.xml
```

### Development Workflow

Recommended workflow during development:

1. **While coding**: Run fast tests frequently
   ```bash
   test-fast
   ```

2. **Before commit**: Run full test suite with coverage
   ```bash
   test-cov
   ```

3. **Before PR**: Run all tests including slow ones
   ```bash
   test
   ```

4. **Performance validation**: Run benchmarks
   ```bash
   test-performance
   ```


## Cairn Stage 3 Test Suite

Run these commands from the repository root to validate Stage 3 orchestration contracts.

```bash
# Unit coverage for Stage 3 primitives
uv run pytest tests/cairn/test_agent.py tests/cairn/test_queue.py tests/cairn/test_watcher.py

# Integration coverage for orchestrator/workspace/signal processing
uv run pytest tests/cairn/test_orchestrator.py tests/cairn/test_workspace.py tests/cairn/test_signals.py

# Optional end-to-end smoke (headless)
uv run pytest tests/cairn/test_e2e_smoke.py
```

Expected outcomes:
- All unit and integration tests pass locally with no skips.
- The optional e2e smoke test passes and confirms spawn → reviewing → accept/reject flow.
- If a local environment is slow, `test_orchestrator.py` may take slightly longer due to async lifecycle polling.

## Cairn Stage 4 Neovim Plugin Tests

Run these commands from the repository root to validate the Stage 4 Neovim plugin implementation.

```bash
# 1) Make sure plugin docs help tags can be generated
nvim --headless -u NONE -c "helptags src/cairn/nvim/doc" -c "qa"

# 2) Run full Stage 4 contract suite (requires plenary.nvim on runtimepath)
PLENARY_PATH=/path/to/plenary.nvim \
  nvim --headless -u src/cairn/nvim/tests/minimal_init.lua \
  -c "set rtp+=$PLENARY_PATH" \
  -c "PlenaryBustedDirectory src/cairn/nvim/tests { minimal_init = 'src/cairn/nvim/tests/minimal_init.lua' }" \
  -c "qa"

# 3) Optional: run focused specs while iterating
PLENARY_PATH=/path/to/plenary.nvim \
  nvim --headless -u src/cairn/nvim/tests/minimal_init.lua \
  -c "set rtp+=$PLENARY_PATH" \
  -c "PlenaryBustedFile src/cairn/nvim/tests/commands_spec.lua { minimal_init = 'src/cairn/nvim/tests/minimal_init.lua' }" \
  -c "PlenaryBustedFile src/cairn/nvim/tests/config_spec.lua { minimal_init = 'src/cairn/nvim/tests/minimal_init.lua' }" \
  -c "PlenaryBustedFile src/cairn/nvim/tests/tmux_spec.lua { minimal_init = 'src/cairn/nvim/tests/minimal_init.lua' }" \
  -c "PlenaryBustedFile src/cairn/nvim/tests/ghost_spec.lua { minimal_init = 'src/cairn/nvim/tests/minimal_init.lua' }" \
  -c "PlenaryBustedFile src/cairn/nvim/tests/watcher_spec.lua { minimal_init = 'src/cairn/nvim/tests/minimal_init.lua' }" \
  -c "qa"
```

Expected outcome:
- Help tags generate cleanly for `src/cairn/nvim/doc/cairn.txt`.
- Stage 4 specs pass for command registration, config/keymaps, tmux preview behavior, ghost text rendering, and watcher parsing/review detection.
