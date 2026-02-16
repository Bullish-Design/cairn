# Refactoring Step 6: Orchestrator Refactoring

## Context
With providers and new FSdantic/Grail APIs in place, the orchestrator should be simplified and aligned to the new lifecycle flow. The execution loop should be provider-based, `.pym` file-driven, and use workspace managers for file/overlay/materialization.

This step corresponds to **Section 6** of `CAIRN_REFACTOR_V2.md`.

## Goal
Refactor the agent execution flow to: generate code via provider, write `.pym`, validate via Grail, execute, collect submission, materialize preview, and accept/reject via overlay manager.

## Requirements
1. **Execution flow**
   - Lifecycle phases: `GENERATING → EXECUTING → SUBMITTING → REVIEWING`.
   - Use `code_provider.get_code` + `validate_code`.
   - Write `.pym` to `.grail/agents/{agent_id}/task.pym`.
   - Load with `grail.load()` and validate with `script.check()`.
   - Run via `script.run(inputs=..., externals=...)`.

2. **External function creation**
   - Build externals using workspace managers (`files`, `kv`, etc.).
   - Provide helpers: `read_file`, `write_file`, `list_dir`, `file_exists`, `search_files`, `search_content`, `submit_result`, `log`.

3. **Accept/Reject logic**
   - Accept uses `stable.overlay.merge(agent_fs, strategy=MergeStrategy.OVERWRITE)`.
   - Reject discards overlay and cleans up.

4. **Error handling**
   - Handle `CodeProviderError`, `grail.ExecutionError`, `grail.InputError` explicitly.
   - Ensure invalid code or validation failures are surfaced cleanly.

5. **Preview materialization**
   - Use `agent_fs.materialize.to_disk(...)` to generate preview workspace.

## Files Likely Impacted
- `src/cairn/orchestrator.py`
- `src/cairn/external_functions.py`
- `src/cairn/agent.py` (if state/lifecycle constants change)

## Acceptance Criteria
- Execution flow uses provider + `.pym` + Grail v2 validation.
- External function wiring relies on new FSdantic managers.
- Accept/reject uses overlay manager.
- Preview materialization uses FSdantic materializer.

## Reference Flow
```python
await self._transition_state(ctx, AgentState.GENERATING)
code = await self.code_provider.get_code(...)
# validate → write .pym → grail.load → check → run
await self._transition_state(ctx, AgentState.SUBMITTING)
await self._collect_submission(ctx)
await self._transition_state(ctx, AgentState.REVIEWING)
await self._materialize_preview(ctx)
```
