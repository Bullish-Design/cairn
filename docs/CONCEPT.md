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

- Stable workspace remains the source of truth.
- Code executes in isolated overlays with copy-on-write semantics.
- Changes are previewed before integration.
- Humans accept (merge into stable) or reject (discard).

This model prioritizes workspace isolation and explicit human control over automatic merging.

## Principles

1. **Copy-on-write over merge complexity**
   Code executes in isolated overlays; integration is explicit accept/reject.

2. **Isolation over implicit trust**
   Execution is sandboxed with no direct system access; all operations go through external functions.

3. **Materialized preview over hidden state**
   Outputs are inspectable as real files/workspaces before integration.

4. **Human authority over automation**
   Code can propose changes; only humans finalize what enters stable.

5. **Pluggable code sources**
   Code can come from files, LLMs, git repos, registries, or custom providers.

## Constraints

- All code must run with strict sandbox boundaries.
- Stable state is never mutated without explicit human acceptance.
- Review must remain cheap: fast preview, clear diffs, reversible decisions.
- Tooling should work with normal editor/test/build workflows.
- Core library remains lightweight and dependency-minimal; extensions live in plugins.

## Reading order for contributors

1. [README.md](../README.md) for setup and first run.
2. `CONCEPT.md` (this file) for intent and invariants.
3. [SPEC.md](SPEC.md) for exact architecture and contracts.
