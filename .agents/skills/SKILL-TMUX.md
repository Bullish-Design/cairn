# SKILL: TMUX preview workflow

Use this skill when changing preview pane/session behavior.

Architecture context lives in [SPEC.md](../../SPEC.md).

## Workflow

1. Preserve stable-vs-preview split as the default interaction model.
2. Ensure preview pane creation is idempotent.
3. Re-target existing preview pane when switching agents.
4. Keep failures non-destructive (never mutate stable workspace).
5. Validate Neovim commands still route through tmux helpers.
