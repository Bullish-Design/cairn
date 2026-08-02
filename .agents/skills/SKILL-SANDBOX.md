# SKILL: bwrap sandbox workflow

Use this skill when changing agent execution or the sandbox API surface.

Architecture context lives in [CONCEPT.md](../../docs/CONCEPT.md) and [SPEC.md](../../docs/SPEC.md).

## Workflow

1. Confirm the sandbox boundary remains strict (no host FS/network/process access; only the materialized workspace is writable).
2. Modify the sandbox API in `src/cairn/runtime/sandbox/boot.py` (functions injected as globals into task code) and keep `BwrapExecutor` re-import semantics in sync (`src/cairn/runtime/sandbox/sandbox.py`).
3. Ensure execution limits are still enforced: host-side wall-clock timeout on the subprocess, plus rlimits applied by the bootstrap (`RLIMIT_DATA`/`RLIMIT_AS`, `RLIMIT_CPU`, recursion depth).
4. Validate agent lifecycle transitions still reach `REVIEWING` or `ERRORED` deterministically (see integration tests).
5. Update `SPEC.md` if the sandbox API or execution contract changes.

## Sandbox invariants

- Stock CPython inside `bwrap --unshare-all --clearenv`; runtime mounted read-only from a declarative Nix store closure manifest (`pkgs.writeClosure` in `devenv.nix`), falling back to `/nix/store` + conventional system dirs.
- The workdir (`$CAIRN_HOME/workspaces/{agent_id}`) is the only writable bind; it doubles as the review preview.
- Host-side re-import never follows symlinks and never imports the `.cairn/` scaffolding.
- Changeset re-import: added/changed files are written to the agent overlay, deleted files are tombstoned with `files.remove`.
