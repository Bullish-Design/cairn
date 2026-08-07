# Cairn Concept

Cairn is a workspace-aware orchestration runtime for sandboxed code execution: **execute code in isolated workspaces, preview changes, and humans control integration**.

## Canonical scope of this document

`CONCEPT.md` owns:
- the collaboration metaphor,
- the product principles,
- the safety and UX constraints.

For implementation details and runtime contracts, use [SPEC.md](SPEC.md).

## Core metaphor: a pile, not branches

A cairn is a pile of stones where each traveler adds to a shared structure.

The shared structure is the **actual Git working tree** — the canonical source
of truth.  Each traveler works on a **disposable real copy** of the tree
(copy-on-write where the filesystem supports it):

- The working tree remains canonical; nothing is mirrored into a database.
- Code executes in disposable real workspaces; writes never touch the tree.
- The computed changeset (files/directories/symlinks/modes changed) is previewed
  before integration; the agent's summary is advisory, the diff is truth.
- Humans accept (apply the changeset to the tree) or reject (discard the
  workspace); every accept revalidates its base under a project lock.

This model prioritizes workspace isolation and explicit human control over
automatic merging.

## Principles

1. **Copy-on-write over merge complexity**
   Code executes in isolated overlays; integration is explicit accept/reject.
   Materialization uses a true reflink where the filesystem supports it
   (btrfs, xfs with reflink, bcachefs) and a plain copy otherwise; the
   executor reports the observed materialization mode per run, so a degraded
   mode is measured, not hidden.

2. **Isolation over implicit trust**
   Code executes inside a bubblewrap sandbox with no network, an unprivileged
   uid, and only the materialized workspace writable.  Bubblewrap is the
   security boundary — task code is ordinary Python with the full standard
   library, and the `read_file`/`write_file` helpers are ergonomics, not a
   sandbox.  Anything that must not be reachable has to be excluded at the
   mount layer.

3. **Materialized preview over hidden state**
   Outputs are inspectable as real files/workspaces before integration.

4. **Human authority over automation**
   Code can propose changes; only humans finalize what enters the working tree.
   Acceptance revalidates the base fail-closed and snapshots pre-apply content
   so decisions stay reversible (`cairn undo`).

5. **Pluggable code sources**
   Code can come from files, LLMs, git repos, registries, or custom providers.

## Constraints

- All code must run with strict sandbox boundaries.
- The working tree is never mutated without explicit human acceptance.
- Review must remain cheap: fast preview, clear diffs, reversible decisions.
- Tooling should work with normal editor/test/build workflows — accepted work
  lands in the real tree where Git, editors, and build tools can see it.
- Containment is proportional, not perfect: kernel escape resistance is out of
  scope and documented as such.
- Core library remains lightweight and dependency-minimal; extensions live in plugins.

## Reading order for contributors

1. [README.md](../README.md) for setup and first run.
2. `CONCEPT.md` (this file) for intent and invariants.
3. [SPEC.md](SPEC.md) for exact architecture and contracts.
