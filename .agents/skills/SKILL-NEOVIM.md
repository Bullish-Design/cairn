# SKILL: Neovim plugin workflow

Use this skill when changing plugin commands, keymaps, watcher hooks, or preview UX.

Architecture context lives in [CONCEPT.md](../../CONCEPT.md) and [SPEC.md](../../SPEC.md).

## Workflow

1. Keep command surface consistent (`Queue`, `Accept`, `Reject`, `Preview`, status/list commands).
2. Ensure configuration defaults are backward-compatible.
3. Keep watcher events scoped to Cairn state/previews.
4. Verify ghost/preview cues do not silently apply agent changes.
5. Run plugin contract tests after edits.
