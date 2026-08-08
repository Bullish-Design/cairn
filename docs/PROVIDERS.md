# Cairn Code Providers

Cairn sources executable code through pluggable `CodeProvider` implementations. Providers resolve a `reference` string into `.py` code and validate it before execution.

## Built-in Providers

### `FileCodeProvider` (default)
- Loads `.py` files from disk.
- `reference` is a path to a `.py` script (extension optional).

Example:
```bash
cairn spawn scripts/refactor_imports.py
```

### `InlineCodeProvider`
- Treats `reference` as the code itself.
- Useful for ad-hoc scripts or testing.

Example (inline run or daemon started with `--provider inline`):
```bash
cairn run "print('hello')" --provider inline
```

## Plugin Providers

Plugin providers register entry points under `cairn.providers` and are loaded by name.

### `GitCodeProvider` (`cairn-git`)
- Loads `.py` files from git references.
- `reference` uses the `git://` scheme and a fragment for the file path.

Example (daemon started with `--provider git`):
```bash
cairn up --provider git
cairn queue "git://github.com/org/scripts?ref=main#tasks/cleanup.py"
```

### `RegistryCodeProvider` (`cairn-registry`)
- Loads `.py` files from a remote registry.
- `reference` uses the `registry://` scheme or a relative path with `--provider-base-path`.

Example (daemon started with `--provider registry`):
```bash
cairn up --provider registry
cairn queue "registry://registry.example.com/scripts/format.py"
```

### `PytuinCodeProvider` (`cairn-pytuin`)
- Loads task code from the [Atuin](https://atuin.sh) KV store via [pytuin](https://github.com/BullishDesign/pytuin).
- `reference` is `namespace/key`, or a bare `key` for the default namespace.
  Segments starting with `-` are rejected: they would be parsed as flags by
  the atuin CLI (e.g. `--help` returns atuin's help text as task code).
- Configuration comes from the environment (the plugin loader passes no
  plugin-specific flags): `CAIRN_PYTUIN_NAMESPACE` (default `cairn`) and
  `CAIRN_PYTUIN_EXECUTABLE` (default `atuin`).
- Requires **Python >= 3.14** — pytuin uses PEP 758 syntax that does not
  parse on 3.13. Cairn core stays 3.13; install this plugin on a 3.14
  interpreter.
- Atuin KV is synced state, so task code may originate on any synced host.
  Accepted by design: bwrap is the security boundary and task code is
  treated as untrusted regardless of provenance.

Example (daemon started with `--provider pytuin`):
```bash
atuin kv set --namespace tasks --key deploy "write_file('out.txt', 'hi')"
cairn up --provider pytuin
cairn queue tasks/deploy
```

Or inline, no daemon needed:
```bash
cairn run tasks/deploy --provider pytuin
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
