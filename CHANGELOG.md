# Changelog

All notable changes to cairn are documented in this file.

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

## [0.2.1] - 2026-08-01

- Baseline release (bwrap executor, orchestrator lifecycle, CLI).
