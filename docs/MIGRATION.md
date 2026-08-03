# Cairn V2 Migration Guide

Cairn V2 shifts from AI-specific orchestration to a general-purpose sandboxed code runtime. This guide summarizes the key updates and how to align integrations.

## 1. Update Dependencies

- Require `fsdantic>=0.7.0` (overlay tombstones, read-only mode, busy
  timeout / content-size caps), `pydantic>=2.0.0`.
- `fsdantic>=0.6.0` consumes the Bullish-Design/agentfs SDK fork
  (`v0.6.4-pyturso-0.7.2`, pyturso 0.7.2); cairn no longer declares
  `agentfs-sdk` directly — it resolves through fsdantic's fork so the
  lockfile carries pyturso 0.7.2.
- Remove any LLM-specific dependencies from the core `cairn` package.
- Grail/Monty dependencies (`grail`, `pydantic-monty`) are no longer required — execution uses stock CPython inside a bubblewrap sandbox.

## 2. Use Code Providers

- Replace direct LLM generation with a `CodeProvider` implementation.
- Use built-in providers (`file`, `inline`) or install plugin providers (`llm`, `git`, `registry`).

Example:
```python
from cairn import CairnOrchestrator
from cairn.providers import FileCodeProvider

orchestrator = CairnOrchestrator(code_provider=FileCodeProvider())
```

## 3. Adopt Sandbox Execution Flow

- Code is plain Python (no restricted dialect, no `@external` declarations, no `from grail import ...`).
- The agent workspace is materialized to a real directory under `$CAIRN_HOME/workspaces/{agent_id}` and the code runs as stock CPython inside a `bwrap` sandbox (`BwrapExecutor`).
- After execution the sandbox changeset is re-imported into the agent overlay; submissions are read from `.cairn/submission.json`.

## 4. Use Workspace Managers

- Prefer `workspace.files`, `workspace.kv`, `workspace.overlay`, and `workspace.materialize`.
- Avoid legacy `open_with_options`, raw fs access, or `FileOperations` helpers.

## 5. Sandbox API

- Task scripts call the sandbox API directly (no declarations): `read_file`, `write_file`, `list_dir`, `file_exists`, `search_files`, `search_content`, `submit_result`, `log`.
- Scripts may also use `delete_file(path)`.  Deletions are re-imported as
  overlay tombstones (`overlay.tombstone`, fsdantic >= 0.7.0), so files that
  exist only in stable can be deleted too; the accept merge applies the
  markers to stable (`MergeResult.tombstones_applied`).
Scripts must call `submit_result(summary, changed_files)` to record a submission for review.

## 6. Review/Accept Flow

- Accept merges overlay changes into `stable.db`.
- Reject discards overlay changes.
- Preview materializations live under `$CAIRN_HOME/workspaces/{agent_id}` (the sandbox workdir doubles as the preview).
