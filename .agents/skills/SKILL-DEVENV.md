# SKILL: devenv/Nix module workflow

Use this skill when changing `modules/*.nix` integration.

Architecture context lives in [README.md](../../README.md) for setup and [SPEC.md](../../SPEC.md) for runtime contracts.

## Workflow

1. Keep module import path and shell UX minimal.
2. Ensure environment defaults are safe and overridable.
3. Ensure processes/scripts reflect current CLI contract.
4. Verify `devenv shell` + service startup still work.
5. Update README quickstart if command entrypoints change.
