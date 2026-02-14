# Testing Guide

## Running Tests from Repository Root

The nixbox repository is configured as a UV workspace with agentfs-pydantic as a member package. You can run all tests from the root directory.

### Quick Start

```bash
# Run all tests
uv run pytest

# Run unit tests only
uv run pytest agentfs-pydantic/tests/test_models.py

# Run with coverage
uv run pytest --cov=agentfs-pydantic/src/agentfs_pydantic --cov-report=term-missing

# Run fast tests (skip slow & benchmarks)
uv run pytest -m "not slow and not benchmark"
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

1. **Unit Tests** (`test_models.py`)
   - Pydantic model validation
   - Edge cases and serialization
   - 100% coverage target

2. **Integration Tests** (`test_integration.py`)
   - Real AgentFS SDK integration
   - Overlay semantics validation
   - KV store operations
   - View query system

3. **Performance Tests** (`test_performance.py`)
   - File operation benchmarks
   - Query performance validation
   - Large file handling
   - Marked with `@pytest.mark.benchmark`

4. **Property-Based Tests** (`test_property_based.py`)
   - Hypothesis-based testing
   - Overlay isolation invariants
   - Roundtrip properties
   - Marked with `@pytest.mark.slow`

### Test Markers

Tests can be filtered by markers:

```bash
# Run only benchmark tests
uv run pytest -m benchmark

# Skip slow tests
uv run pytest -m "not slow"

# Run only integration tests
uv run pytest agentfs-pydantic/tests/test_integration.py
```

### Coverage Reports

Coverage reports are generated in multiple formats:

- **Terminal**: Shows missing lines inline
- **HTML**: Browse detailed report at `htmlcov/index.html`
- **XML**: For CI/CD integration at `coverage.xml`

### Configuration

Test configuration is defined in:

- `pyproject.toml` - Workspace-level pytest configuration
- `agentfs-pydantic/pytest.ini` - Package-level configuration
- `devenv.nix` - Test scripts for devenv shell

### Troubleshooting

**Import errors**: Make sure dependencies are synced
```bash
uv sync --all-extras
```

**Module not found**: Ensure you're in the repository root
```bash
cd /path/to/nixbox
uv run pytest
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
uv sync --all-extras

# Run full test suite with coverage
uv run pytest \
  --cov=agentfs-pydantic/src/agentfs_pydantic \
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
nvim --headless -u NONE -c "helptags cairn/nvim/doc" -c "qa"

# 2) Run full Stage 4 contract suite (requires plenary.nvim on runtimepath)
PLENARY_PATH=/path/to/plenary.nvim \
  nvim --headless -u cairn/nvim/tests/minimal_init.lua \
  -c "set rtp+=$PLENARY_PATH" \
  -c "PlenaryBustedDirectory cairn/nvim/tests { minimal_init = 'cairn/nvim/tests/minimal_init.lua' }" \
  -c "qa"

# 3) Optional: run focused specs while iterating
PLENARY_PATH=/path/to/plenary.nvim \
  nvim --headless -u cairn/nvim/tests/minimal_init.lua \
  -c "set rtp+=$PLENARY_PATH" \
  -c "PlenaryBustedFile cairn/nvim/tests/commands_spec.lua { minimal_init = 'cairn/nvim/tests/minimal_init.lua' }" \
  -c "PlenaryBustedFile cairn/nvim/tests/config_spec.lua { minimal_init = 'cairn/nvim/tests/minimal_init.lua' }" \
  -c "PlenaryBustedFile cairn/nvim/tests/tmux_spec.lua { minimal_init = 'cairn/nvim/tests/minimal_init.lua' }" \
  -c "PlenaryBustedFile cairn/nvim/tests/ghost_spec.lua { minimal_init = 'cairn/nvim/tests/minimal_init.lua' }" \
  -c "PlenaryBustedFile cairn/nvim/tests/watcher_spec.lua { minimal_init = 'cairn/nvim/tests/minimal_init.lua' }" \
  -c "qa"
```

Expected outcome:
- Help tags generate cleanly for `cairn/nvim/doc/cairn.txt`.
- Stage 4 specs pass for command registration, config/keymaps, tmux preview behavior, ghost text rendering, and watcher parsing/review detection.
