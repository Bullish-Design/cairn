# Changelog

All notable changes to cairn are documented in this file.

## [Unreleased]

### Fixed

- **`cairn undo` refused every accept that had created a file.**  Undo drift
  detection read presence as drift for `delete_paths` (paths that did not
  exist before the accept), so undoing a file the agent added raised
  `UNDO_STALE_BASE` on a completely untouched tree.  The added path's
  accepted state is *presence* (undo = delete); drift is now absence, or a
  content change since the accept, validated against the post-apply digests
  that were already recorded.  Added undo-after-add and undo-after-add-then-
  edit tests (the existing undo tests only covered pre-existing files, which
  is why CI was green).
- **`--project-root` / `--cairn-home` / provider flags given *before* the
  subcommand were silently discarded.**  The recursive subparser copies used
  `default=None`, and argparse writes subparser defaults into the same
  namespace after the parent has parsed, clobbering the parent's value.
  `cairn --project-root /other/repo accept agent-x` applied the changeset to
  the *current* directory.  Subparser copies now use
  `default=argparse.SUPPRESS` so they only write the attribute when actually
  supplied; both flag positions bind (with a CLI test asserting so).
- **A second `cairn up` crashed with a raw `turso.Error` traceback.**
  `initialize()` opens `bin.db` before binding the control socket, so the
  exclusive-lock failure surfaced as `WorkspaceError` and escaped the
  `except RuntimeError` handler.  `_run_up` now catches `WorkspaceError` and
  reports the already-running daemon instead of dumping a traceback.
- **Provider errors rendered `[PROVIDERERROR]` instead of `[PROVIDER_ERROR]`.**
  The default error code is the uppercased class name; `ProviderError` now
  returns the snake-cased code.

## [0.4.0] - 2026-08-07

Execution hot-path refactor.  Overhead on a 200-file project drops from
133-159 ms to 103 ms, and `capture_manifest` on the cairn repo itself from
373 ms to 31 ms (12x).  The bwrap boundary, the authority machinery
(accept/undo/journal/lock), the provider model and the daemon/queue
architecture are unchanged.

### Fixed

- **A task could forge deletions into its own changeset.**  The post-run
  workspace capture built its `ProjectFilter` from the *workspace*, whose
  `.gitignore` files the sandboxed task writes.  A task whose entire body was
  `open('.gitignore','w').write('important.py\n')` produced
  `deleted: ['important.py']` for a file it never touched and that still
  existed in the workspace; on accept that file was removed from the working
  tree.  Admission rules are now host state, built once per run from the
  project tree and rebound (never rebuilt) for the workspace capture, so
  nothing the task writes can steer the computed changeset.  The changeset is
  the authoritative record of what the agent did, and it is now
  agent-independent in fact as well as in intent.
- Real-sandbox test runtime resolution lived in three divergent copies, two
  carrying the same dead check (`"/nix/store" in Path.parts` is never true).
  Consolidated into `tests/cairn/sandbox_env.py`; an unresolvable runtime now
  warns instead of skipping silently, and `CAIRN_REQUIRE_SANDBOX_TESTS=1`
  (set by the devenv gate) makes it an error.  A bare `pytest` run went from
  266 passed / 12 skipped to 278 passed / 0 skipped — the previously-dead
  tests, including the sandbox boundary suite, all pass.

### Performance

- **Manifest walk is pruned at excluded directories** rather than scanning
  and discarding.  On this repo it visited 6,285 paths to keep 167; it now
  visits 167.  Pruning uses a *hereditary* predicate (excluded dir names and
  gitignore, which also exclude descendants) kept separate from the recording
  predicate, so the non-hereditary suffix rule still behaves correctly for a
  directory named `*.pyc`.  Manifests are byte-identical to the previous
  implementation, verified against the real repo and an adversarial fixture.
- **One `ProjectFilter` per task instead of three.**  Its constructor
  `rglob`-ed the whole unfiltered tree for `.gitignore` files (18.7 ms a
  time); that discovery walk is now pruned too.
- **Admission checks no longer construct `pathlib` objects per path** — the
  check cost 27.8 us against a 2.8 us `lstat`, i.e. 10x the syscall it
  guarded.  String-based now, validated against the previous implementation
  as an oracle.
- **Materialization uses a true reflink (`FICLONE`)** with a plain-copy
  fallback, and reports the observed mode per run via `MaterializeStats`.
  Note this is a modest win, not the large one first assumed:
  `copy_file_range` was already reflinking on btrfs.  `FICLONE` is ~1.75x
  faster on large files and, more usefully, *predictable* — it either
  reflinks or fails with a distinguishable errno, whereas `copy_file_range`
  degrades silently, which is what makes the reported mode meaningful.

### Added

- `tests/cairn/test_performance_execpath.py` — benchmarks that run the real
  `BwrapExecutor`.  The existing `test_performance.py` stubs the executor and
  cannot observe capture, materialize, diff or sandbox cost; that blind spot
  is why none of the above was caught earlier.  Its `BenchmarkExecutor` now
  says so explicitly.

### Changed

- A task-authored `.gitignore` affects *subsequent* runs, not its own: the
  base manifest was captured under the previous rules, and a diff is only
  meaningful between two manifests taken under the same admission rules.
- `docs/CONCEPT.md` principle 1 now states where copy-on-write actually
  applies (reflink-capable filesystems) and that the mode is reported;
  `docs/SPEC.md` documents admission rules as host state.

## [0.3.0] - 2026-08-02

### Dependencies

- **fsdantic v0.3.1 -> v0.7.0** (git tag).  Brings overlay tombstones,
  read-only workspace mode, `busy_timeout_ms` / `max_content_bytes` open
  options, `KVManager.increment`, `Workspace.serialized()`, and base-union
  `include_base` query/search.
- Dropped the direct `agentfs-sdk` PyPI dependency: fsdantic 0.7.0 carries the
  Bullish-Design `agentfs` SDK fork (`v0.6.4-pyturso-0.7.2`) with
  `pyturso 0.7.2`, which upstream's `pyturso==0.4.4` pin cannot coexist with.
- `uv.lock` is now tracked in version control for reproducible installs.

### Added

- **Sandbox tombstones** — `BwrapExecutor._reimport` records deletions via
  `overlay.tombstone()` (fsdantic >= 0.7.0), so files that exist only in
  stable can be deleted by an agent; the accept merge replays the markers
  against stable (`MergeResult.tombstones_applied`).
- `accept_agent` returns merge statistics (`files_merged`,
  `tombstones_applied`); `_handle_accept` surfaces them in the command
  payload and the CLI reports deletions applied to stable.
- `open_workspace` / `WorkspaceManager.create_workspace` / `open_workspace`
  accept `busy_timeout_ms` (default 5000) and `max_content_bytes`
  (default unbounded) and forward `readonly` (previously accepted but
  silently dropped).
- fsdantic `WorkspaceError` codes (`WORKSPACE_NOT_FOUND`, `WORKSPACE_READONLY`,
  `CONTENT_TOO_LARGE`) are translated into cairn's `WorkspaceError` instead of
  collapsing into a generic `WORKSPACE_OPEN_FAILED`.
- `LifecycleStore.update_atomic` serializes same-process read-modify-write
  sequences with `Workspace.serialized()`.
- Read-only CLI inspection paths: `workspace info`, `files list/read/search/
  tree`, `preview changes/file` open workspaces with `readonly=True`; the
  stable workspace is now required to exist for `preview changes`.
- Fixed `preview changes` / `preview file` resolving agent databases as
  `agent-{agent_id}.db` when agent ids already carry the `agent-` prefix
  (previews previously could never find a real agent workspace).

### Changed

- MVCC now uses real libSQL MVCC journaling (`PRAGMA journal_mode = "mvcc"`,
  `BEGIN CONCURRENT`) on pyturso 0.7.2 instead of silently falling back to
  WAL.  The driver does not reliably surface write-write conflicts
  (last-write-wins); use `Workspace.serialized()` or the repository CAS for
  atomic read-modify-write sequences.

### Tests

- New `test_fsdantic_features.py` (30): tombstones, sandbox re-import
  tombstones, read-only mode, `busy_timeout_ms`, `max_content_bytes`,
  `kv.increment`, `serialized()`, `include_base` union, open-seam config.
- New `test_materialize_live_fixture.py` (4): end-to-end materialize-to-disk
  with a real on-disk fixture tree (`tests/fixtures/sample_project`) covering
  overlay-on-base materialization, tombstone-at-merge semantics, the sandbox
  re-import roundtrip, and the orchestrator accept flow.
- `test_concurrency.py` MVCC assertions updated for the real `mvcc` journal.
- `test_sandbox_executor.py` deletion test now covers overlay-owned **and**
  stable-only deletions through the real bwrap sandbox and verifies the
  accept merge applies both tombstones.

### CI (act / GitHub Actions)

- `.github/workflows/ci.yml` runs the project's own gate (`devenv ci`:
  `ty check` + full pytest including the real bubblewrap sandbox tests) on
  push/PR.  Runs identically on GitHub hosted runners (Determinate Nix
  installer) and locally with `act` (`.actrc` maps `ubuntu-latest` to a
  Nix-enabled image and skips the GitHub-only installers).
- `.actrc` persists the container `/nix` in the `cairn-act-nix` Docker volume
  so devenv's one-time CPython source build (its pinned nixpkgs rev is not
  cached upstream) sticks across local runs — steady-state `act` runs take
  ~1.5 minutes.

### devenv

- `devenv.lock` updated: devenv module input bumped to 9d93b83 (2.2.x line).
- `devenv.yaml` nixpkgs input switched from `github:cachix/devenv-nixpkgs/rolling`
  (no public cache — verified: no cachix cache exists for the fork, and its
  pinned rev's CPython build is absent from cache.nixos.org, forcing a
  ~10-minute source compile in CI) to `github:NixOS/nixpkgs/nixpkgs-unstable`
  (rev 104240a7, whose python3.13 output substitutes from cache.nixos.org).
- `devenv.lock` is now tracked in git so CI resolves the exact same
  nixpkgs/devenv inputs as local development.

### Runtime

- `AgentStateManager.increment` (and `increment_turn`) now use the workspace
  KV manager's atomic `increment` (fsdantic >= 0.5.0), so concurrent
  increments cannot lose updates; a non-numeric stored value still resets to
  0 first (legacy behavior preserved).
- Removed dead `copy_workspace_to_submission` helper from
  `orchestrator_helpers.py` (unused since the bwrap refactor).

### Tests / benchmarks

- Timing benchmarks are deselected in the default suite
  (`-m "not benchmark"` in `pyproject.toml`) and enforce real targets only
  under `CAIRN_STRICT_BENCHMARKS=1`; otherwise thresholds are
  environment-tolerant (5x) so loaded machines do not fail them.
- New `test_state.py`: namespacing, atomic increments, non-numeric reset,
  and a 20-way concurrent increment race asserting zero lost updates.

## [0.2.1] - 2026-08-01

- Baseline release (bwrap executor, orchestrator lifecycle, CLI).
