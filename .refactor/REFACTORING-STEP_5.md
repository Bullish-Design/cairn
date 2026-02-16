# Refactoring Step 5: Code Provider Architecture

## Context
Cairn’s core currently assumes all code is produced by an LLM. The refactor introduces a **pluggable CodeProvider abstraction** that can source code from files, inline strings, LLMs, git, registries, etc. This abstraction is the primary architectural change enabling Cairn to be general-purpose.

This step corresponds to **Section 5** of `CAIRN_REFACTOR_V2.md`.

## Goal
Introduce a `CodeProvider` protocol in core, implement built-in providers (file + inline), and update the orchestrator to use a provider for code retrieval and validation.

## Requirements
1. **Create providers module**
   - New file: `src/cairn/providers.py`.
   - Define `CodeProvider` protocol with:
     - `get_code(reference: str, context: dict[str, Any]) -> str`
     - `validate_code(code: str) -> tuple[bool, str | None]` (default valid).

2. **Implement built-in providers**
   - `FileCodeProvider`: loads `.pym` from disk, supports `base_path`.
   - `InlineCodeProvider`: treats `reference` as code.
   - Raise `CodeProviderError` when code cannot be obtained.

3. **Integrate provider into orchestrator**
   - Add `code_provider: CodeProvider | None` param to `CairnOrchestrator.__init__`.
   - Default to `FileCodeProvider()` if none provided.
   - Use `code_provider.get_code(...)` during generation.
   - Call `code_provider.validate_code(...)` before writing `.pym`.

4. **Provider context**
   - Pass `agent_id`, `workspace`, and `stable` in the context dict.
   - Keep context shape stable for plugin providers.

## Files Likely Impacted
- `src/cairn/providers.py` (new)
- `src/cairn/orchestrator.py`
- CLI/commands that instantiate orchestrator

## Acceptance Criteria
- `CodeProvider` protocol exists and is used by orchestrator.
- File and inline providers are implemented with basic validation.
- Orchestrator uses provider for code retrieval and validation.
- No LLM code generation is required in core.

## Reference Snippets
```python
@runtime_checkable
class CodeProvider(Protocol):
    async def get_code(self, reference: str, context: dict[str, Any]) -> str: ...
    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        return (True, None)
```

```python
class FileCodeProvider:
    def __init__(self, base_path: Path | str | None = None): ...
    async def get_code(self, reference: str, context: dict[str, Any]) -> str: ...
```
