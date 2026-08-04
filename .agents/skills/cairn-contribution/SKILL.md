---
name: cairn-contribution
description: >-
  Contributing to the Cairn codebase itself: the devenv development gate
  (devenv test), test layout and conventions, the SPEC.md source-of-truth
  rule, coding conventions, the security model, performance targets, and
  common pitfalls when modifying src/cairn, tests, or docs. Use when changing
  the Cairn repository (as opposed to using or embedding it).
license: MIT
metadata:
  subsystem: development
---

# Contributing to Cairn

This skill covers working **on the Cairn repository**. For using the library,
see [cairn-library-api](../cairn-library-api/SKILL.md); for running a
deployment, [cairn-cli-operations](../cairn-cli-operations/SKILL.md).

## Environment

The toolchain is managed by **devenv (Nix) + uv**. Python 3.13+.

```bash
devenv shell            # enters the dev shell (declares bwrap + sandbox runtime)
uv sync --all-extras    # rebuild the venv if needed
```

The sandbox runtime is declared in `devenv.nix` and consumed via
`CAIRN_EXECUTOR_*` env vars:
- `CAIRN_EXECUTOR_BWRAP_PATH` — bubblewrap binary.
- `CAIRN_EXECUTOR_PYTHON_PATH` — the sandbox interpreter (stdlib only).
- `CAIRN_EXECUTOR_SANDBOX_CLOSURE_PATH` — the interpreter's Nix store closure
  manifest (one path per line); the executor binds exactly those paths
  read-only. Without it, the executor falls back to `/nix/store` + conventional
  system dirs.

## The gate: `devenv test`

`devenv test` is the repository's full validation gate and runs identically in
CI (`.github/workflows/ci.yml`, runnable locally with `act`):

1. `uv lock --check` — lockfile freshness.
2. `ruff check src tests` + `ruff format --check src tests` (line length 120).
3. `ty check` — type checking (ty, Python 3.13, `src.include = ["src/cairn"]`).
4. `pytest -q --cov=cairn --cov-report=term-missing` — including the
   real-bubblewrap sandbox tests, with a **75% coverage floor**.

Run the whole thing before finishing any change:

```bash
devenv test
```

## Test layout and conventions

```text
tests/
├── cairn/                      # unit-ish tests (async, pytest-asyncio auto mode)
│   ├── conftest.py             # project_root / cairn_home / orchestrator fixtures
│   ├── test_orchestrator.py    # lifecycle, accept/reject/undo flows
│   ├── test_lifecycle.py       # LifecycleStore, recovery, retention
│   ├── test_workspace*.py      # workspace_manager / cache / api
│   ├── test_fsdantic_features.py  # tombstones, readonly, caps, serialized()
│   ├── test_sandbox_executor.py   # change tracking (no bwrap) + real sandbox
│   ├── test_providers.py / test_plugin_providers.py
│   ├── test_state.py, test_queue_limits.py, test_watcher.py, ...
│   └── integration/            # end-to-end; marked @pytest.mark.integration
│       ├── test_e2e_workflows.py      # spawn→review→accept/reject with stub executors
│       ├── test_cli_daemon.py         # signals, daemon pidfile, exit codes
│       ├── test_accept_safety.py      # ACCEPT_STALE_BASE, undo
│       ├── test_sandbox_boundary.py   # adversarial sandbox tests
│       ├── test_crash_recovery.py, test_concurrency.py,
│       ├── test_resource_limits.py, test_failure_injection.py
└── fixtures/sample_project/     # project tree for watcher/materialize tests
```

Conventions:

- Async everywhere; `asyncio_mode = "auto"` in `pyproject.toml`.
- Markers: `integration`, `slow`, `benchmark` (`--strict-markers`).
- **Benchmarks are deselected by default** (`-m "not benchmark"`); run them
  explicitly: `pytest -m benchmark`, or strictly with
  `CAIRN_STRICT_BENCHMARKS=1 pytest -m benchmark`.
- The `orchestrator` fixture in `conftest.py` uses `InlineCodeProvider` and
  `max_concurrent_agents=1`; integration tests inject `executor_factory`
  stubs (`StubExecutor`) to avoid needing bwrap for every flow.
- Real-sandbox tests skip when `bwrap` is unavailable
  (`CAIRN_TEST_BWRAP` / `CAIRN_EXECUTOR_BWRAP_PATH` / `shutil.which("bwrap")`),
  but the devenv gate fails loudly if the env vars are missing so the suite
  can't silently skip in CI.
- Target runs: `pytest tests/cairn/test_orchestrator.py`,
  `pytest -m benchmark tests/cairn/test_performance.py`, etc.

## Coding conventions

- **Type hints everywhere**; pydantic models for records/commands/settings;
  TypedDicts (`cairn.core.types`) for dict payloads.
- **Typed errors** with machine-readable codes:
  `cairn.core.exceptions` (`CairnError(message, error_code, context)`),
  subclasses `RecoverableError`/`FatalError`/`AgentError`/`WorkspaceError`/... .
  Surface codes like `WORKSPACE_NOT_FOUND`, `ACCEPT_STALE_BASE`,
  `EXECUTION_TIMEOUT`, `QUEUE_FULL`, `WORKSPACE_BUDGET_EXCEEDED`.
- **All magic numbers in `cairn.core.constants`** (limits, timeouts, retry
  params) — never inline.
- Settings live in `cairn.runtime.settings` (pydantic-settings, `CAIRN_*`
  env prefixes: `CAIRN_ORCHESTRATOR_`, `CAIRN_EXECUTOR_`, `CAIRN_PATHS_`).
- Keep blocking I/O out of the event loop (`asyncio.to_thread` for file reads,
  batch syncs, mirror writes).
- **SPEC.md is the source of truth for runtime contracts.** If behavior in
  `src/cairn/*` changes, update `docs/SPEC.md` in the same change. Doc
  boundaries: README = setup + first commands; CONCEPT = philosophy;
  SPEC = contracts; skills = workflows.

## Security model (don't break it)

- **Bubblewrap is the boundary.** Never `exec()`/`eval()` untrusted code in
  the orchestrator process — always route through `BwrapExecutor`.
- Keep the sandbox strict: `--unshare-all --clearenv --new-session`, unprivileged
  uid/gid (nobody), only the materialized workspace writable, runtime mounted
  read-only. The sandbox API helpers are ergonomics, not a security boundary —
  exclude anything sensitive at the mount layer.
- Host-side re-import must **never follow symlinks** and must never import the
  `.cairn/` scaffolding.
- Use subprocess with list args (no shell strings); validate paths; respect
  resource limits (memory, CPU, file size, process count, descriptors,
  workspace budget). Watch for regex/ReDoS: `search_content` caps pattern
  length; `REGEX_TIMEOUT_SECONDS` constant exists for host-side use.
- Preserve adversarial coverage: `tests/cairn/integration/test_sandbox_boundary.py`.

## Performance targets

Critical paths (keep these in mind when changing them):

- File sync (watch event → stable.db): <10 ms.
- Agent spawn: <1 s.
- Preview open: <100 ms.
- Accept/reject: <50 ms.

Non-critical: LLM generation <5 s, materialization <500 ms. Benchmarks live in
`tests/cairn/test_performance.py` (deselected by default; CI runs a separate
non-blocking job with relaxed thresholds).

## Common pitfalls

1. **Legacy workspace APIs** — use `workspace.files` / `workspace.kv` /
   `workspace.overlay` / `workspace.materialize`; never
   `workspace.raw.fs.read_file(...)` or `FileOperations(...)`.
2. **Opening `bin.db` in a second process** — pyturso locks exclusively even
   read-only. Queries must go through the lifecycle mirror
   (`open_lifecycle_readonly`), never `Fsdantic.open(bin.db)`.
3. **SQLite lock contention** — keep re-import writes sequential with a
   retry on transient locks; use `Workspace.serialized()` for same-process
   read-modify-write; prefer `kv.increment` for counters (atomic per key).
4. **Workspace leaks** — close every opened workspace; use `WorkspaceManager`
   or the `WorkspaceCache` (which pins in-use workspaces so eviction can't
   close them mid-run).
5. **Blocking the loop** — file reads, syncs, and mirror writes go through
   `asyncio.to_thread` or batch helpers.
6. **State transitions** — only move through `VALID_TRANSITIONS`
   (`AgentContext.transition`); persist lifecycle on every transition and
   rewrite the mirror.
7. **Signal races** — claim by atomic rename to `*.processing`; write signals
   atomically (temp + rename); quarantine failures instead of deleting them.
8. **Untrusted input in accept** — keep the staleness check
   (`_detect_stale_paths` against `base_hashes`) and the undo snapshot in
   front of every merge.

## Related

- Gate + toolchain: `devenv.nix`, `scripts/cairn-test.sh`, `pyproject.toml`.
- Testing doc: [TESTING](../../../docs/TESTING.md).
- Historical implementation plan (mostly complete): `docs/REFACTOR_PLAN.md`.
- Changelog: `CHANGELOG.md`; version bump in `pyproject.toml` per semver.
