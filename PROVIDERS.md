# Cairn Code Providers

Cairn sources executable code through pluggable `CodeProvider` implementations. Providers resolve a `reference` string into `.pym` code and optionally validate it before execution.

## Built-in Providers

### `FileCodeProvider` (default)
- Loads `.pym` files from disk.
- `reference` is a path to a `.pym` script (extension optional).

Example:
```bash
cairn spawn scripts/refactor_imports.pym
```

### `InlineCodeProvider`
- Treats `reference` as the code itself.
- Useful for ad-hoc scripts or testing.

Example:
```bash
cairn spawn "print('hello')" --provider inline
```

## Plugin Providers

Plugin providers register entry points under `cairn.providers` and are loaded by name.

### `LLMCodeProvider` (`cairn-llm`)
- Generates `.pym` code from natural language prompts.
- `reference` is a task description.

Example:
```bash
cairn spawn "Add docstrings" --provider llm
```

### `GitCodeProvider` (`cairn-git`)
- Loads `.pym` files from git references.
- `reference` uses the `git://` scheme and a fragment for the file path.

Example:
```bash
cairn spawn "git://github.com/org/scripts?ref=main#tasks/cleanup.pym" --provider git
```

### `RegistryCodeProvider` (`cairn-registry`)
- Loads `.pym` files from a remote registry.
- `reference` uses the `registry://` scheme or a relative path with `--provider-base-path`.

Example:
```bash
cairn spawn "registry://registry.example.com/scripts/format.pym" --provider registry
```

## Writing a Custom Provider

Implement the `CodeProvider` protocol and register an entry point:

```toml
[project.entry-points."cairn.providers"]
custom = "my_package.provider:CustomCodeProvider"
```

Provider interface:
```python
class CustomCodeProvider(CodeProvider):
    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        ...

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        ...
```

The orchestrator supplies `context` with the agent ID and workspaces, so providers can inspect project state if needed.
