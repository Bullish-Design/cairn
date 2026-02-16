# Refactoring Step 8: Plugin Ecosystem

## Context
Cairn core must remain dependency-light and AI-agnostic. Advanced providers (LLM, Git, Registry) live in separate plugin packages. This step defines their structures and integration expectations.

This step corresponds to **Section 8** of `CAIRN_REFACTOR_V2.md`.

## Goal
Define and scaffold plugin packages (or at minimum their structure and integration points), ensuring core remains provider-agnostic and the CLI can select providers via flags.

## Requirements
1. **Core package stays clean**
   - No LLM dependencies in `cairn` core.
   - Core includes only File and Inline providers.

2. **Plugin package structures**
   - `cairn-llm/` with `cairn_llm/provider.py` and prompts.
   - `cairn-git/` with `cairn_git/provider.py` + cache helpers.
   - `cairn-registry/` with `cairn_registry/provider.py` + client helpers.

3. **Provider implementations**
   - Each plugin provider implements the `CodeProvider` protocol.
   - `LLMCodeProvider` generates `.pym` code and validates structure.
   - `GitCodeProvider` loads `.pym` files from `git://...` references.
   - `RegistryCodeProvider` loads code from HTTP registry.

4. **CLI integration**
   - `cairn spawn ... --provider llm` should be supported when plugin installed.
   - Provider selection may map to entrypoints or explicit imports.

## Files/Packages Likely Impacted
- Core: `src/cairn/cli.py`
- External repos/packages for plugins (if implemented in this repo, keep separate directories).

## Acceptance Criteria
- Core runs without LLM dependencies.
- Provider selection is possible without changing core internals.
- Plugin structures match the architecture document.
- Plugin providers are isolated in their own packages.
