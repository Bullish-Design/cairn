# Refactoring Step 7: File Structure Changes

## Context
The refactor introduces new core modules and removes legacy ones. Files should be reorganized to match the new architecture (providers, external functions, etc.) and old LLM/Grail v1 artifacts should be removed from core.

This step corresponds to **Section 7** of `CAIRN_REFACTOR_V2.md`.

## Goal
Align the repository structure with the new architecture: add new modules (`providers.py`, `external_functions.py`), remove deprecated ones, and ensure imports are updated throughout the codebase.

## Requirements
1. **Add new files**
   - `src/cairn/providers.py` (CodeProvider protocol + built-ins).
   - `src/cairn/external_functions.py` (external function factory).

2. **Remove deprecated files**
   - `code_generator.py` (LLM code generation moved to plugin).
   - `agent_tools.py` (replaced by external_functions factory).
   - Any Grail v1-specific integration modules.

3. **Update orchestrator imports**
   - Use `from cairn.providers import CodeProvider, FileCodeProvider`.
   - Use `from cairn.external_functions import create_external_functions` (if introduced).

4. **Ensure `.grail/agents` path**
   - Use `.grail/agents/{agent_id}/task.pym` as the canonical output location.

5. **CLI updates**
   - Ensure the CLI exposes a `--provider` flag or equivalent provider selection path.

## Files Likely Impacted
- `src/cairn/orchestrator.py`
- `src/cairn/cli.py`
- `src/cairn/__init__.py`
- Removed modules (`code_generator.py`, `agent_tools.py`)

## Acceptance Criteria
- New files exist and are imported correctly.
- Deprecated files are removed or unused.
- All imports compile and tests still pass.
- Repo layout matches the V2 diagram in `CAIRN_REFACTOR_V2.md`.
