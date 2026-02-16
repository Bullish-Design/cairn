# Refactoring Step 4: Grail Integration Changes

## Context
Cairn currently uses Grail v1 `MontyContext` with string-based code generation and runtime-only validation. The refactor requires adopting Grail v2 with `grail.load()` and `.pym` files, plus a pre-flight `check()` validation step.

This step corresponds to **Section 4** of `CAIRN_REFACTOR_V2.md`.

## Goal
Replace `MontyContext` usage with `grail.load()` and `.pym` file execution. Ensure every agent code run is validated via `script.check()` prior to execution, and errors are surfaced cleanly.

## Requirements
1. **Remove MontyContext**
   - Delete imports and setup for `MontyContext`.
   - Remove any complex context construction logic.

2. **Adopt `.pym` execution flow**
   - Load `.pym` file via `grail.load(path)`.
   - Call `script.check()` before `script.run(...)`.
   - Handle validation errors by transitioning to an error state.

3. **Inputs / externals**
   - Use `script.run(inputs=..., externals=...)`.
   - Inputs should include `task_description` (string task). 
   - Externals should be the orchestrator-generated external functions.

4. **Error handling**
   - Update exception handling to Grail v2 types:
     - `grail.ExecutionError`
     - `grail.InputError`
   - Provide a clear error path for validation failures.

5. **Grail artifacts**
   - Ensure `.grail/agents/{agent_id}/task.pym` is the canonical code path.
   - Support writing `check.json` or any grail artifacts if required by library defaults.

## Files Likely Impacted
- `src/cairn/orchestrator.py`
- `src/cairn/external_functions.py`
- Any Grail or Monty integration helper modules
- Tests around agent execution

## Acceptance Criteria
- No `MontyContext` usage remains.
- All agent executions load a `.pym` file and validate via `script.check()`.
- Validation errors prevent execution and are surfaced clearly.
- Execution uses `script.run(inputs=..., externals=...)`.

## Reference Snippets
**Old API (to remove):**
```python
from grail import MontyContext
context = MontyContext(...)
result = await context.execute_async(code)
```

**New API (target):**
```python
import grail
script = grail.load(".grail/agents/{agent_id}/task.pym")
check_result = script.check()
if not check_result.valid:
    # handle validation errors
    return
result = await script.run(
    inputs={"task_description": task},
    externals=external_functions,
)
```
