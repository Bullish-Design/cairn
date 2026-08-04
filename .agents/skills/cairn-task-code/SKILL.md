---
name: cairn-task-code
description: >-
  How to write Python task scripts that run inside Cairn's bubblewrap sandbox:
  the nine injected sandbox helpers (read_file, write_file, list_dir,
  file_exists, delete_file, search_files, search_content, submit_result, log),
  path rules, resource limits, the submission contract, and debugging via
  run.log. Use when writing or reviewing task scripts that Cairn will execute.
license: MIT
metadata:
  subsystem: sandbox
---

# Writing Cairn Task Code

Task scripts are **plain Python** that run as stock CPython inside a bubblewrap
sandbox. There is no restricted dialect, no imports of a Cairn SDK, and no
`@external` declarations — the sandbox helpers are injected as globals by the
bootstrap (`cairn.runtime.sandbox.boot`, shipped into the workspace as
`.cairn/boot.py`).

The security boundary is **bubblewrap, not the helper functions**. Task code has
the full standard library and can bypass the helpers with raw `open()`/`os`
calls — it just cannot escape the sandbox: no network, no host filesystem, no
other processes, only the materialized workspace writable.

## Where the code runs

```text
$CAIRN_HOME/workspaces/{agent_id}/
├── .cairn/
│   ├── task.py            # your code (written by the provider pipeline)
│   ├── task.json          # inputs: {"task_description": ...}
│   ├── boot.py            # bootstrap (host-owned, read-only to you in practice)
│   ├── submission.json    # written by submit_result()
│   └── run.log            # stdout + stderr captured by the host
└── ...                    # materialized workspace files (stable + overlay)
```

- The workspace root is mounted at `/workspace`; `CAIRN_WORKSPACE` is set, and
  `os.chdir(WORKSPACE)` is done before your code runs, so raw relative paths
  work too.
- Inputs from `.cairn/task.json` are injected as globals — so
  `task_description` is directly available, and the whole inputs dict is also
  in a global named `inputs`.

## Sandbox API (available as globals)

All paths are **relative to the workspace root**. Absolute paths and `..`
traversal raise `ValueError`.

| Function | Signature | Semantics |
|---|---|---|
| `read_file` | `read_file(path) -> str` | Text read (UTF-8, errors replaced). Raises `FileNotFoundError` if absent, `ValueError` if > 10 MB. |
| `write_file` | `write_file(path, content) -> bool` | Text write, creates parent dirs. Returns `True`. |
| `list_dir` | `list_dir(path='.') -> list[str]` | Sorted entry names. |
| `file_exists` | `file_exists(path) -> bool` | Existence check (invalid paths → `False`). |
| `delete_file` | `delete_file(path) -> bool` | Deletes a file. `False` if missing; `ValueError` on directories. Deletion becomes an overlay **tombstone** on re-import — stable-only files can be deleted too. |
| `search_files` | `search_files(pattern) -> list[str]` | Glob (`fnmatch`) over workspace files → relative paths. |
| `search_content` | `search_content(pattern, path='.') -> list[dict]` | Line-based regex search → `[{"file", "line", "text"}]`. Pattern ≤ 1000 chars. |
| `submit_result` | `submit_result(summary, changed_files) -> bool` | Records the review submission (see below). |
| `log` | `log(message) -> bool` | Prints to stdout (goes to `run.log`). |

## The submission contract

`submit_result(summary, changed_files)` writes `.cairn/submission.json`, which
the host reads back and stores in the agent's KV as the review payload. Rules:

- **Call it before the script exits.** If the script never calls it, the
  bootstrap writes an implicit submission with `summary = task_description` and
  `changed_files = []`.
- **Be accurate about `changed_files`.** The host compares your claim against
  the *actual* changeset (computed from the filesystem snapshot). A mismatch
  sets `claim_mismatch` on the record, and `cairn status` / `cairn-cli agent
  status` prints a warning showing what you claimed vs. what actually changed.
- The actual changeset (written/deleted files, run log, base hashes) is the
  ground truth used for the review, the accept staleness check, and undo.

## Resource limits (enforced inside the sandbox)

- **Wall-clock**: host-side timeout `max_execution_time` (default **60 s**) —
  `CairnTimeoutError` with `EXECUTION_TIMEOUT`; the process is killed.
- **Memory**: `RLIMIT_DATA` (falls back to `RLIMIT_AS`), default 100 MB.
- **CPU**: `RLIMIT_CPU` (same seconds as the wall-clock timeout).
- **Largest single file**: `RLIMIT_FSIZE`, default 64 MB.
- **Processes/threads**: `RLIMIT_NPROC`, default 64 (fork bombs are capped).
- **Open descriptors**: `RLIMIT_NOFILE`, default 1024.
- **Recursion depth**: `sys.setrecursionlimit`, default 1000.
- **Workspace budget**: post-run, if the sandbox wrote more than
  `max_workspace_bytes` (default 512 MB), the run fails with
  `WORKSPACE_BUDGET_EXCEEDED`.

## Environment facts

- **No network.** `bwrap --unshare-all`: net namespace unshared, no sockets.
- **No host filesystem.** Only the materialized workspace is writable; the
  interpreter runtime is mounted read-only (closure manifest on NixOS, or
  `/nix/store` + conventional dirs otherwise).
- **No inherited environment.** `--clearenv`; only the host's `CAIRN_*`
  sandbox variables are re-set.
- **No terminal.** `--new-session`, stdin is `/dev/null`; `sys.stdin.isatty()`
  is `False` and `/dev/tty` cannot be opened.
- **No non-stdlib imports.** The sandbox Python is stdlib-only (empty
  site-packages on NixOS). Only import the standard library.
- Symlinks are **never followed** by the host re-import; a file replaced by a
  symlink is treated as deleted. Executable bits and empty directories set in
  the sandbox **are** preserved (recorded in the run record and re-applied on
  the next materialization).

## Example task script

```python
# .cairn/task.py — inputs injected: task_description, inputs
content = read_file("src/main.py")

# do the work with plain Python + helpers
lines = content.splitlines()
lines.insert(0, "# generated by cairn task")
write_file("src/main.py", "\n".join(lines))

# optional: search across the workspace
matches = search_content("TODO", path="src")

log(f"updated src/main.py; found {len(matches)} TODOs")
submit_result(
    summary="Prefixed src/main.py with a generation marker",
    changed_files=["src/main.py"],
)
```

A minimal no-op that just records a submission:

```python
submit_result(summary="no changes made", changed_files=[])
```

## Debugging

- The sandbox's stdout/stderr land in
  `$CAIRN_HOME/workspaces/{agent_id}/.cairn/run.log` — the single best place
  to look when a run fails. A syntax error or raised exception prints a normal
  Python traceback there and the agent transitions to `ERRORED`.
- `cairn logs <agent-id>` (or `cairn-cli agent logs <agent-id>`) prints the run
  log for both errored and reviewed agents.
- `sys.exit(n)` is honored: non-zero exit fails the run (`SANDBOX_EXECUTION_FAILED`).
- `submit_result` returning `False`/errors: the host tolerates a missing or
  invalid `submission.json` (falls back to the task description), but an
  accurate submission makes review much easier.

## Do / Don't

- ✅ Use `read_file`/`write_file`/`search_*` for anything inside the workspace
  (they give you the clear errors and stay within the ergonomic contract).
- ✅ Call `submit_result` with a real summary and the real changed-file list.
- ✅ Write small files; stay well under the 64 MB per-file cap and the
  workspace budget.
- ❌ Don't import anything beyond the stdlib — it won't be there.
- ❌ Don't try to reach the network, `/etc`, the host project, or other
  processes — that's exactly what the sandbox prevents.
- ❌ Don't write into `.cairn/` — it is host-owned scaffolding and is excluded
  from the re-import snapshot.
- ❌ Don't use pathological regexes in `search_content` (pattern length is
  capped at 1000 chars; CPU time is bounded by `RLIMIT_CPU`).

## Related

- Sandbox policy details: [SPEC § Execution contracts](../../../docs/SPEC.md).
- Execution implementation: `src/cairn/runtime/sandbox/sandbox.py`.
- Bootstrap / API definitions: `src/cairn/runtime/sandbox/boot.py`.
