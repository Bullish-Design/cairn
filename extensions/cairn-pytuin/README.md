# cairn-pytuin

Atuin KV code provider plugin for [Cairn](https://github.com/BullishDesign/cairn).
Sources agent task code from the Atuin KV store through
[pytuin](https://github.com/BullishDesign/pytuin), so

```bash
cairn queue tasks/deploy
```

fetches the script stored at `atuin kv get --namespace tasks deploy` and runs
it in the sandbox.

## Python version

Requires **Python >= 3.14** — this is not a soft preference. pytuin uses
PEP 758 syntax (`except A, B:` without parentheses), which is 3.14-only, so
`import pytuin` fails on 3.13 with a `SyntaxError`. Cairn itself stays on
3.13; this plugin sits on the extension boundary and needs its own 3.14
interpreter.

## Install

From a sibling checkout (recommended while cairn/pytuin are unpublished):

```bash
uv sync --extra dev          # resolves cairn + pytuin from the checkouts
```

Or with pip, pointing at the same checkouts:

```bash
python3.14 -m venv .venv
.venv/bin/pip install -e /path/to/cairn -e /path/to/pytuin -e .
```

Once the provider is installed, cairn discovers it through the
`cairn.providers` entry point — no cairn core changes needed.

## Configuration

| Environment variable          | Default   | Meaning                          |
| ----------------------------- | --------- | -------------------------------- |
| `CAIRN_PYTUIN_NAMESPACE`      | `cairn`   | Namespace for bare-key references |
| `CAIRN_PYTUIN_EXECUTABLE`     | `atuin`   | `atuin` binary name or path       |

These are read from the environment because cairn's plugin loader passes only
`project_root`/`base_path` to a provider constructor; there is no channel for
plugin-specific CLI flags.

## References

```
namespace/key   ->  atuin kv get --namespace <namespace> <key>
key             ->  default namespace (CAIRN_PYTUIN_NAMESPACE, default "cairn")
```

Anything else — empty segments, more than one `/`, whitespace, leading or
trailing slashes, or a segment starting with `-` — is rejected with an
actionable error. The `-` rule matters because segments are passed to
`atuin kv get` as bare argv elements: a reference like `--help` would
otherwise be parsed as a flag by the atuin CLI and its help text returned
as task code.

## Usage

```bash
# store a task (once, on any synced host)
atuin kv set --namespace tasks --key deploy \
  "write_file('out.txt', 'hi')
submit_result('done', ['out.txt'])"

# inline run, no daemon needed
cairn run tasks/deploy --provider pytuin

# or daemonized
cairn up --provider pytuin
cairn queue tasks/deploy
```

## Behavior notes

- **Missing key vs. backend down.** pytuin returns `None` both when a key is
  absent and when the daemon is dead, timed out, or the CLI exits nonzero. On
  a `None` result this provider probes daemon health and reports either
  `No task stored at ns/key` (key genuinely absent) or `Atuin daemon is
  unreachable` (infrastructure problem), so an outage is never mistaken for a
  typo.
- **Synced state is accepted by design.** Atuin KV syncs across machines, so
  task code can arrive from any synced host. That is fine: bwrap is the
  security boundary and task code is treated as untrusted regardless of
  provenance.
- **Validation.** `validate_code` rejects empty values and anything that is
  not syntactically valid Python. What the code *does* is the sandbox's job,
  never judged here.
- **No write path.** This provider is read-only (`kv_get`). Publishing tasks
  to KV is a separate surface and is deliberately not part of the
  `CodeProvider` protocol.

## Development

The plugin needs its own Python 3.14 environment (see Python version above)
and resolves cairn/pytuin from sibling checkouts, so tests run via uv:

```bash
uv sync --frozen --extra dev      # 3.14 venv, uses the committed uv.lock
.venv/bin/pytest -q               # unit tests; fake client, no Atuin needed
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

CI runs this suite in the `extension-tests` job (it checks out a pinned
pytuin beside the repo), and `devenv ci` runs it locally when the pytuin
checkout is present. The root `pytest` never collects these tests — root
`testpaths` is `tests/`, and the 3.14 requirement keeps the plugin out of
the root devenv venv — so do not rely on the root suite to cover the
plugin.
