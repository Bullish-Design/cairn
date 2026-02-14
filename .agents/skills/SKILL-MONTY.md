# SKILL: Monty sandbox workflow

Use this skill when changing agent execution or external function bindings.

Architecture context lives in [CONCEPT.md](../../CONCEPT.md) and [SPEC.md](../../SPEC.md).

## Workflow

1. Confirm sandbox boundary remains strict (no direct host I/O/import escape).
2. Add or modify external function declarations and implementations together.
3. Ensure execution limits/timeouts are still enforced.
4. Validate agent lifecycle transitions still reach `REVIEWING` or `FAILED` deterministically.
5. Update `SPEC.md` if the external function contract changes.
