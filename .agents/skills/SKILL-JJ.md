# SKILL: Jujutsu integration workflow

Use this skill when changing jj mapping for accept/reject flows.

Architecture context lives in [SPEC.md](../../SPEC.md).

## Workflow

1. Keep mapping aligned with overlay outcomes:
   - Accept -> squash/integrate.
   - Reject -> abandon/discard.
2. Handle missing jj gracefully when integration is optional.
3. Keep state mapping (`agent_id <-> change_id`) consistent and recoverable.
4. Verify command error paths are surfaced to orchestrator status.
