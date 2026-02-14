# Cairn

Cairn is an orchestration system for AI code agents with isolated workspace overlays and explicit human control.

## Read this first (canonical docs)

1. **README.md** (this file): install + quickstart.
2. **[CONCEPT.md](CONCEPT.md)**: philosophy and constraints.
3. **[SPEC.md](SPEC.md)**: current architecture and runtime contracts.

If a topic appears in multiple places, `CONCEPT.md` and `SPEC.md` are authoritative.

## Installation

### 1) Import modules in `devenv.nix`

```nix
{ inputs, ... }:
{
  imports = [
    ./modules/agentfs.nix
    ./modules/cairn.nix
  ];
}
```

### 2) Enter shell and verify AgentFS

```bash
devenv shell
agentfs-info
```

### 3) Start Cairn orchestrator

```bash
cairn up
```

## Quickstart

### Queue work

```bash
cairn spawn "Add docstrings to public functions"
```

### Inspect

```bash
cairn list-agents
cairn status agent-<id>
```

### Resolve

```bash
cairn accept agent-<id>
# or
cairn reject agent-<id>
```

## Neovim plugin quick setup

Point your plugin manager to `src/cairn/nvim`.

```lua
{
  dir = '~/path/to/cairn/src/cairn/nvim',
  config = function()
    require('cairn').setup({
      preview_same_location = true,
    })
  end,
}
```

## Contributing

- Workflow instructions: [AGENT.md](AGENT.md)
- Skill runbooks: [`.agent/skills/`](.agent/skills)
- Architecture and contracts: [CONCEPT.md](CONCEPT.md), [SPEC.md](SPEC.md)
