# Cairn Concept

Cairn is the philosophy layer for Nixbox: **humans and agents work in parallel, and humans stay in control of integration**.

## Canonical scope of this document

`CONCEPT.md` owns:
- the collaboration metaphor,
- the product principles,
- the safety and UX constraints.

For implementation details and runtime contracts, use [SPEC.md](SPEC.md).

## Core metaphor: a pile, not branches

A cairn is a pile of stones where each traveler adds to a shared structure.

- Humans keep editing the stable workspace.
- Agents propose changes in isolated overlays.
- Humans accept (copy into stable) or reject (discard).

This model prioritizes continuity of human flow over shared-cursor AI interaction.

## Principles

1. **Copy-on-write over merge complexity**  
   Agent proposals are isolated overlays; integration is explicit accept/reject.

2. **Isolation over implicit trust**  
   Agent execution is sandboxed and mediated through explicit host functions.

3. **Materialized preview over hidden state**  
   Agent outputs are inspectable as real files/workspaces before integration.

4. **Human authority over automation**  
   Agents can propose; only humans finalize what enters stable.

## Constraints

- Agent code must run with strict sandbox boundaries.
- Stable state is never mutated by an agent without explicit acceptance.
- Review must remain cheap: fast preview, clear diffs, reversible decision.
- Tooling should work with normal editor/test/build workflows.

## Reading order for contributors

1. [README.md](README.md) for setup and first run.
2. `CONCEPT.md` (this file) for intent and invariants.
3. [SPEC.md](SPEC.md) for exact architecture and contracts.
