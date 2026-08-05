---
name: cairn-code-providers
description: >-
  Extending Cairn with pluggable code sources: the CodeProvider protocol, the
  built-in file and inline providers, plugin entry-point registration under
  the cairn.providers group, the provider context dict, and validation
  semantics. Use when adding a custom code provider or wiring a provider into
  the orchestrator or CLI.
license: MIT
metadata:
  subsystem: providers
---

# Cairn Code Providers

Cairn sources the executable code for each agent through a pluggable
`CodeProvider`. Providers resolve a `reference` string into Python source code
and validate it before execution (the orchestrator calls `validate_code`
unconditionally). The orchestrator never generates code itself — that is
always the provider's job.

Canonical reference: [PROVIDERS](../../../docs/PROVIDERS.md) and
`src/cairn/providers/providers.py`.

## The protocol

```python
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class CodeProvider(Protocol):
    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        """Return executable Python source for the given reference."""
        raise NotImplementedError

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        """Pre-flight validation; the orchestrator calls it unconditionally."""
        return True, None
```

- `get_code` must return **source text** (the sandbox runs it as
  `.cairn/task.py`), not a path or an object.
- `validate_code` is the **only pre-flight gate** in the pipeline and is
  **required**. Returning `(False, reason)` aborts the run and transitions
  the agent to `ERRORED`. Syntax errors are not checked here — they surface as
  sandbox tracebacks that also mark the agent `ERRORED`.
- `context` is supplied by the orchestrator per-agent:
  `{"agent_id": str, "workspace": ProjectView, "project_root": Path}` — the
  workspace entry is a **read-only** gitignore-aware snapshot view over the
  canonical tree (`cairn.runtime.driver.ProjectView`); providers never receive
  a writable workspace or database, so they cannot mutate project state.
- Raise `ProviderError` (or `cairn.core.exceptions.ProviderError`) for
  user-facing failures; `get_code` failures are recorded on the agent as its
  error message.
- Transient failures: the built-in `FileCodeProvider` retries reads on
  `RecoverableError`, `TimeoutError`, and `ConnectionError` (via
  `cairn.utils.retry.with_retry`). Plugin providers can follow the same
  pattern with `PROVIDER_RETRY_EXCEPTIONS`.

## Built-in providers

### FileCodeProvider (default)

```python
from cairn.providers import FileCodeProvider

provider = FileCodeProvider(base_path="/path/to/scripts")
```

- `reference` is a path to a Python script; a missing suffix is defaulted to
  `.py`; relative references resolve against `base_path` (default: the
  orchestrator's `project_root`, so CLI references are project-relative).
- `validate_code` is a no-op returning `(True, None)`.

### InlineCodeProvider

```python
from cairn.providers import InlineCodeProvider

provider = InlineCodeProvider()
code = await provider.get_code("print('hi')", context={})
```

- Treats `reference` itself as the code. No validation.

## Resolution and plugin entry points

`resolve_code_provider(provider, *, project_root, base_path)` returns a
provider by name:

1. `"inline"` → `InlineCodeProvider()`.
2. `"file"` → `FileCodeProvider(base_path=base_path or project_root or ".")`.
3. anything else → looks up the **entry-point group `cairn.providers`**:

```toml
# pyproject.toml of a plugin package
[project.entry-points."cairn.providers"]
git = "cairn_git.provider:GitCodeProvider"
registry = "cairn_registry.provider:RegistryCodeProvider"
```

- The entry point must resolve to a **class or callable**. It is instantiated
  with `project_root` and `base_path` kwargs **only if its signature accepts
  them** (introspected via `inspect.signature`) — so simple zero-arg factories
  keep working.
- Unknown name → `ProviderError("Unknown provider ... install the plugin")`.
- Multiple entry points with the same name → `ProviderError` (ambiguous).

## CLI wiring

Both CLIs accept `--provider <name>` and `--provider-base-path <path>`:

```bash
# file provider, project-relative reference (default)
cairn queue scripts/refactor.py

# inline provider — the reference IS the code
cairn spawn "print('hello')" --provider inline
```

Programmatically, pass a `CodeProvider` instance to the orchestrator:

```python
from cairn import CairnOrchestrator
from cairn.providers import FileCodeProvider

orchestrator = CairnOrchestrator(project_root=".", code_provider=FileCodeProvider())
```

## Reference interpretation by provider

| Provider | `reference` meaning | Example |
|---|---|---|
| `file` | Path to a Python script (project-relative or absolute) | `scripts/refactor_imports.py` |
| `inline` | The code itself | `"print('hi')"` |
| `git` (plugin) | Git URL with path fragment | `git://github.com/org/repo:script.py` |
| `registry` (plugin) | Registry reference | `registry://org/script:version` |

## Writing a custom provider — checklist

1. Implement `get_code` (async, returns source text) and optionally
   `validate_code`.
2. Raise `ProviderError` for user-facing failures so the message lands on the
   agent's error field.
3. If the provider needs the project root or a base path, accept
   `project_root`/`base_path` constructor kwargs — the loader passes them when
   the signature allows.
4. Register the entry point under `cairn.providers` in the plugin's
   `pyproject.toml`.
5. Test with `tests/cairn/test_plugin_providers.py` conventions (see
   [cairn-contribution](../cairn-contribution/SKILL.md)).

## Related

- Provider docs: [PROVIDERS](../../../docs/PROVIDERS.md).
- Orchestrator usage: `src/cairn/orchestrator/orchestrator.py`
  (`_generate_code` phase).
- Plugin test patterns: `tests/cairn/test_plugin_providers.py`,
  `tests/cairn/test_providers.py`.
