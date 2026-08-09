# The Cairn demo — a runnable walkthrough

`demo/` is a self-verifying walkthrough of Cairn.  One command drives *real*
agents through the full lifecycle against a throwaway fixture project and
emits a single markdown file, `demo/out/WALKTHROUGH.md`, with narration
interleaved with **actually captured** output.

What's in the file is what ran.  Every chapter calls `prove()`; a claim that
no longer holds fails the run with a non-zero exit — the walkthrough cannot
silently rot.

```bash
python -m demo
```

That runs `cairn doctor` (aborting with setup guidance if the sandbox
runtime is not trustworthy), builds the fixture, runs all five acts (Act IV
starts and stops its own daemon), and writes `demo/out/WALKTHROUGH.md`.
Scratch state (fixture, homes, workspaces) lives only under `demo/out/`,
which is gitignored; pass `--keep` to retain it after a run.

## Flags

| flag | meaning |
|---|---|
| `--only <id>` | run a single chapter (e.g. `--only 07`) |
| `--act <0-4>` | run a single act |
| `--keep` | retain `demo/out/` scratch state after the run |
| `--no-daemon` | skip Act IV (daemon & CLI) — the CI job's choice for stability |
| `--include-recovery` | also run ch20 (recovery after a daemon death); off by default, skips loudly |
| `--out <path>` | transcript file path (default `demo/out/WALKTHROUGH.md`) |

The runner refuses to start if `CAIRN_PATHS_*` is set (the demo manages its
own project root and home under `demo/out/`), and every orchestrator asserts
it is bound to the fixture before spawning — the demo can never operate on
your checkout.

## What each act proves

| act | chapters | proves |
|---|---|---|
| **0 — Trust the runtime** | 00 `cairn doctor` | the sandbox runtime works by *launching a real sandbox*, not by inspecting config |
| **I — The core loop** | 01 fixture, 02 providers, 03 run, 04 untouched tree, 05 review, 06 accept, 07 undo, 08 reject | the headline: agents run against a disposable copy; nothing reaches the tree until a human accepts; every accept is reversible |
| **II — Where Cairn earns its keep** | 09 the agent lies, 10 fail-closed accept, 11 boundary, 12 limits, 13 changeset steering, 14 concurrency | the diff is truth, accept is fail-closed and reversible, limits are enforced and legible, admission rules are host state |
| **III — Embedding APIs** | 15 inspector, 16 state manager, 17 driver/capability, 18 queue+retry | Cairn as a library — no daemon, no sandbox, all sub-second |
| **IV — Daemon & CLI** | 19 daemon + thin client, 20 recovery | the CLI never constructs an orchestrator; the daemon owns the databases and the socket |

A committed sample transcript from a full run lives in
[`docs/sample-walkthrough.md`](sample-walkthrough.md) — generated output,
with absolute paths relativized, for browsing on GitHub.

## CI

The workflow runs `python -m demo --no-daemon --out /tmp/WALKTHROUGH.md`
inside the devenv shell: non-zero on any failed `prove()`, Act IV skipped
for stability.  This is what stops the walkthrough rotting — if a chapter's
claim stops holding, CI goes red.
