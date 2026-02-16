# Refactoring Step 9: Migration Path

## Context
The V2 refactor is multi-phase and touches dependencies, provider abstraction, FSdantic/Grail integration, orchestrator flow, tests, and documentation. This step captures the ordered migration plan from Section 9 of `CAIRN_REFACTOR_V2.md`.

## Goal
Provide a concrete migration checklist to execute the refactor in the recommended sequence, ensuring dependencies and APIs are updated without breaking the build.

## Migration Phases
1. **Dependencies**
   - Update `pyproject.toml` to `fsdantic>=0.3.0`, `grail>=2.0.0`, `pydantic>=2.0.0`.
   - Remove LLM dependencies from core.

2. **Provider abstraction**
   - Add `CodeProvider` protocol.
   - Implement file + inline providers.
   - Update orchestrator to accept a provider.

3. **Extract LLM to plugin**
   - Create `cairn-llm` package.
   - Move `code_generator.py` into plugin as `LLMCodeProvider`.
   - Update prompts for `.pym` generation.
   - Add CLI provider selection (`--provider`).

4. **FSdantic integration**
   - Replace `open_with_options` with `open`.
   - Replace file/kv/raw ops with workspace managers.
   - Adopt `overlay` and `materialize` managers.

5. **Grail integration**
   - Remove `MontyContext`.
   - Use `grail.load()` and `.pym`.
   - Add `check()` validation and update exception handling.

6. **Orchestrator refactor**
   - Update execution flow for providers.
   - Simplify external function creation.
   - Update accept/reject logic.

7. **Tests**
   - Update fixtures for new APIs.
   - Add provider tests.
   - Add grail check tests.

8. **Documentation**
   - Update README and SPEC.
   - Add provider docs + migration guide.

## Acceptance Criteria
- Migration steps are applied in the above order.
- Each phase leaves the codebase in a runnable state.
- Core remains AI-agnostic at the end of the migration.
