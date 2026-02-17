# Cairn V2 Migration Guide

Cairn V2 shifts from AI-specific orchestration to a general-purpose sandboxed code runtime. This guide summarizes the key updates and how to align integrations.

## 1. Update Dependencies

- Require `fsdantic>=0.3.0`, `grail>=2.0.0`, `pydantic>=2.0.0`.
- Remove any LLM-specific dependencies from the core `cairn` package.

## 2. Use Code Providers

- Replace direct LLM generation with a `CodeProvider` implementation.
- Use built-in providers (`file`, `inline`) or install plugin providers (`llm`, `git`, `registry`).

Example:
```python
from cairn import CairnOrchestrator
from cairn.providers import FileCodeProvider

orchestrator = CairnOrchestrator(code_provider=FileCodeProvider())
```

## 3. Adopt `.pym` Execution Flow

- Write code to `.pym` files under `.grail/agents/{agent_id}/task.pym`.
- Validate scripts with `grail.load(...).check()` before execution.

## 4. Use Workspace Managers

- Prefer `workspace.files`, `workspace.kv`, `workspace.overlay`, and `workspace.materialize`.
- Avoid legacy `open_with_options`, raw fs access, or `FileOperations` helpers.

## 5. External Functions

- Use `create_external_functions` for Grail/Monty execution.
- Ensure scripts call `submit_result(summary, changed_files)` to report results.

## 6. Review/Accept Flow

- Accept merges overlay changes into `stable.db`.
- Reject discards overlay changes.
- Preview materializations live under `$CAIRN_HOME/workspaces/{agent_id}`.
