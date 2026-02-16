# Refactoring Step 10: Testing Strategy

## Context
The refactor changes core APIs, execution flow, providers, and FSdantic/Grail integrations. Testing must cover provider behavior, workspace manager behavior, and the full agent lifecycle with `.pym` execution.

This step corresponds to **Section 10** of `CAIRN_REFACTOR_V2.md`.

## Goal
Define and implement tests that validate the new provider architecture, FSdantic workspace manager usage, Grail validation integration, and full lifecycle behavior.

## Required Test Coverage
1. **Provider tests**
   - `FileCodeProvider`: loads `.pym` files correctly.
   - `InlineCodeProvider`: returns code unchanged.

2. **Workspace manager tests**
   - `workspace.files.read/write` paths.
   - `workspace.overlay.merge` behavior.

3. **Orchestrator + provider integration**
   - Spawn agent with file provider.
   - Ensure `.grail/agents/{agent_id}/task.pym` exists.
   - Ensure grail `check.json` is written if applicable.
   - Ensure preview is materialized.

4. **Plugin tests (if plugins are present)**
   - LLM provider validation checks.
   - Git provider loads correct files.
   - Registry provider fetches code.

## Reference Examples
```python
async def test_file_code_provider():
    provider = FileCodeProvider(base_path="./test_scripts")
    code = await provider.get_code("example.pym", {})
    assert "from grail import" in code
```

```python
async def test_full_lifecycle_with_file_provider():
    orch = CairnOrchestrator(code_provider=FileCodeProvider(base_path="./test_scripts"))
    await orch.initialize()
    agent_id = await orch.spawn_agent("add_docstrings.pym")
    # wait for REVIEWING, assert preview + .pym
```

## Acceptance Criteria
- Provider tests pass.
- Workspace manager tests pass.
- Lifecycle integration tests pass.
- No tests depend on LLMs unless in plugin packages.
