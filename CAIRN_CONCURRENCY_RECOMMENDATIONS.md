# Cairn Concurrency Recommendations

## Overview

The integration test failures stem from concurrent access to the same AgentFS/Fsdantic
workspace database. AgentFS uses SQLite (via turso). SQLite allows a single writer at a
time, and the current stack does not provide busy timeout handling or request-level
serialization. Under concurrent load:

- Writes collide and raise "database is locked" (turso.Busy / OperationalError).
- Reads also update metadata (e.g., atime), which turns reads into writes and
  increases lock contention.
- The Remora wrapper falls back to the stable workspace on any read error, which
  hides lock failures and produces misleading "file not found" errors.

These symptoms are reproducible in `tests/integration/cairn/test_concurrent_safety.py`
when multiple tasks access the same workspace concurrently.

## Root Cause

1) **SQLite write locking**
   - AgentFS uses a single SQLite database per workspace.
   - Concurrent writes (or read operations that update atime) are serialized by SQLite.
   - Without a busy timeout or retry strategy, concurrent operations fail immediately.

2) **Reads are not read-only**
   - AgentFS updates `fs_inode.atime` on read, so a read holds a write lock.
   - This increases the likelihood of collisions even in mixed read/write workloads.

3) **No application-level serialization in Remora**
   - Remora calls `workspace.files.*` concurrently without guarding access.
   - The AgentFS SDK does not enforce ordering or queueing for a single workspace.

## Immediate Remora-side Mitigation (Implemented)

- Serialize all access to each workspace using an `asyncio.Lock`.
- Use a shared lock for stable workspace access across agents.
- Only fall back to the stable workspace when the error is a missing-file case,
  not on transient SQLite busy errors.

This makes concurrent workloads safe in Remora without changing tests or AgentFS.

## Recommended Upstream Fixes (Cairn / AgentFS)

To eliminate the need for Remora-side locking, the workspace layer should handle
concurrency internally. Recommended changes are listed in priority order.

### 1) Add Busy Timeout and Retry in AgentFS

**Goal:** If SQLite is locked, wait and retry instead of failing.

- Configure SQLite with `busy_timeout` (e.g., 5000 ms).
- In the async wrapper, retry a small number of times with exponential backoff
  for `Busy` / `OperationalError` codes.
- This should apply to all filesystem operations (read, write, exists, list_dir).

### 2) Avoid Write-on-Read Metadata Updates

**Goal:** Reduce write lock pressure for read-heavy workloads.

Options:
- Add a configuration flag to disable `atime` updates entirely for read calls.
- Batch `atime` updates in a background task instead of on every read.
- Store read metadata in a separate in-memory cache to avoid DB writes.

### 3) Provide Per-Workspace Operation Queueing

**Goal:** Guarantee serialization at the workspace layer, not the caller.

- Wrap database operations in a per-workspace `asyncio.Lock` or task queue.
- This ensures all callers (Remora, Cairn, or other clients) get safe access
  without needing external synchronization.

### 4) Enable WAL Mode

**Goal:** Improve concurrency for reads while a writer is active.

- Configure SQLite with WAL mode (`PRAGMA journal_mode=WAL`).
- WAL allows concurrent readers while a write transaction is ongoing.
- Combined with busy timeouts, this improves throughput.

## Suggested Cairn API Enhancements

- `Workspace.open(..., concurrency="serialized")` (default on)
- `Workspace.open(..., busy_timeout_ms=5000)`
- `Workspace.open(..., update_atime=False)`

These options make concurrency policy explicit and move the responsibility to the
workspace implementation rather than each client.

## Validation Plan

Once upstream changes are made, the following tests should pass without any
Remora-specific locks:

- `tests/integration/cairn/test_concurrent_safety.py::test_concurrent_writes_to_same_agent`
- `tests/integration/cairn/test_concurrent_safety.py::test_concurrent_read_write`
- Any concurrent executor workflows that create or modify workspace files.

## Summary

The underlying issue is SQLite lock contention under concurrent access. The
correct place to handle this is inside the workspace layer (Cairn/AgentFS),
by combining busy timeouts, WAL mode, and per-workspace serialization.
