# Refactoring Step 3: FSdantic Integration Changes

## Context
Cairn currently uses deprecated FSdantic APIs (e.g., `Fsdantic.open_with_options`, `FileOperations`, raw `agent_fs.raw` access). The refactor aims to adopt the newer workspace manager APIs (`files`, `kv`, `overlay`, `materialize`) for clearer semantics, better error handling, and type safety.

This step corresponds to **Section 3** of `CAIRN_REFACTOR_V2.md` and should be applied across the core orchestrator and any FSdantic interaction points.

## Goal
Replace legacy FSdantic usage with the new workspace manager APIs, without changing runtime behavior. The orchestrator should rely on `workspace.files`, `workspace.kv`, `workspace.overlay`, and `workspace.materialize` instead of raw access or custom FileOperations wrappers.

## Requirements
1. **Workspace opening**
   - Replace `Fsdantic.open_with_options(AgentFSOptions(...))` with `Fsdantic.open(path=...)`.
   - Avoid `AgentFSOptions` unless specifically needed for new API.

2. **File operations**
   - Replace `agent_fs.raw.fs.*` or `FileOperations` usage with `agent_fs.files.*`.
   - Use built-in query/search APIs:
     - `files.query(ViewQuery(...))`
     - `files.search("**/*.txt")`

3. **KV operations**
   - Replace any direct KV access with `workspace.kv` manager methods.

4. **Overlay operations**
   - Use `workspace.overlay.merge(...)` for accept/merge.
   - Use `workspace.overlay.list_changes(...)` to inspect diffs.
   - Use `workspace.overlay.reset(...)` for reject/cleanup.

5. **Materialization**
   - Replace manual copy/preview code with `workspace.materialize.to_disk(...)`.
   - Use `workspace.materialize.diff(...)` when only a diff is needed.

## Files Likely Impacted
- `src/cairn/orchestrator.py`
- `src/cairn/external_functions.py` (if exists)
- Any helper modules that touch AgentFS raw objects
- Tests that exercise filesystem behavior

## Acceptance Criteria
- No direct references to `agent_fs.raw` remain in the main FS handling paths.
- No `FileOperations` wrapper is used.
- Preview/materialization uses the FSdantic materialization manager.
- Existing behavior is preserved (no functional changes aside from API usage).

## Reference Snippets
**Old API (to remove):**
```python
self.stable = await Fsdantic.open_with_options(
    AgentFSOptions(path=str(self.agentfs_dir / "stable.db"))
)
content = await agent_fs.raw.fs.read_file(path)
```

**New API (target):**
```python
self.stable = await Fsdantic.open(path=str(self.agentfs_dir / "stable.db"))
content = await agent_fs.files.read(path)
```

**Overlay / materialize examples:**
```python
result = await stable.overlay.merge(agent_fs, strategy=MergeStrategy.OVERWRITE)
changes = await stable.overlay.list_changes(agent_fs, path="/")
preview_dir = await agent_fs.materialize.to_disk(preview_dir, base=stable, clean=True)
```
