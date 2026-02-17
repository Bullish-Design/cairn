# Cairn Reorganization Proposal

## Goals

- Make the root directory scannable in under a minute.
- Group like artifacts (docs, tooling, extensions) so intent is obvious.
- Keep user-facing entry points (README, CLI) easy to find.
- Minimize churn and preserve Git history where possible.

## Current Pain Points

- Root is a flat mix of docs, tools, extensions, and code.
- Related docs are scattered (README, CONCEPT, SPEC, PROVIDERS, TESTING).
- Extensions (cairn-llm, cairn-git, cairn-registry) read like peer repos but live at the top level.
- Hard to tell where CLI, orchestrator, and plugin code live within `src/` at a glance.

## Proposal A: Low-Churn Root Grouping

A minimal reorganization that keeps paths mostly intact but groups top-level files.

### Structure

```
.
├── docs/
│   ├── CONCEPT.md
│   ├── SPEC.md
│   ├── PROVIDERS.md
│   ├── TESTING.md
│   ├── MIGRATION.md
│   └── CLI_README.md
├── extensions/
│   ├── cairn-git/
│   ├── cairn-llm/
│   └── cairn-registry/
├── scripts/
├── src/
├── stubs/
├── tests/
├── README.md
├── pyproject.toml
├── devenv.nix
├── devenv.yaml
└── uv.lock
```

### Benefits

- Immediate reduction in top-level clutter.
- Easy to navigate: docs, extensions, code, tooling.
- Lowest migration cost.

### Cost

- Update doc links and tooling references.
- Update extension paths in any automation scripts.

## Proposal B: Domain-Focused Source Layout

Keep Proposal A and also make `src/cairn/` discoverable by grouping by domain.

### Example `src/cairn/` layout

```
src/cairn/
├── cli/
│   ├── __init__.py
│   ├── cli.py
│   └── commands.py
├── orchestrator/
│   ├── __init__.py
│   ├── orchestrator.py
│   ├── lifecycle.py
│   ├── queue.py
│   └── signals.py
├── providers/
│   ├── __init__.py
│   └── providers.py
├── runtime/
│   ├── __init__.py
│   ├── agent.py
│   ├── external_functions.py
│   └── settings.py
├── watcher/
│   ├── __init__.py
│   └── watcher.py
└── nvim/
```

### Benefits

- Clear entry points for CLI, orchestration, providers, runtime, and tooling.
- Easier onboarding for new contributors.
- Aligns with the project’s “clarity over cleverness” principle.

### Cost

- More import path changes (manageable with a single pass).
- Potential updates to tests and docs references.

## Proposal C: Workspace-Ready Monorepo Layout

Treat Cairn as a core + extensions monorepo with explicit package boundaries.

### Structure

```
.
├── packages/
│   ├── cairn-core/        # current src/ + pyproject
│   ├── cairn-git/
│   ├── cairn-llm/
│   └── cairn-registry/
├── docs/
├── scripts/
└── tooling/
    ├── devenv.nix
    ├── devenv.yaml
    └── uv.lock
```

### Benefits

- Explicit package boundaries and dependency management.
- Clear scaling path for future extensions.
- Works well with a workspace build system later.

### Cost

- Highest churn; requires updates to build, CI, and tooling.
- Requires a clear decision on how to manage shared tooling and deps.

## Recommendation

Start with Proposal A for immediate readability wins, then consider Proposal B once the team is comfortable. Proposal C is valuable if extensions are expected to grow and diverge.

## Suggested Decision Criteria

- If we want low churn: Proposal A.
- If we want better code discoverability: Proposal A + B.
- If we plan to grow extensions as separate products: Proposal C.

## Next Steps (If We Proceed)

1. Confirm preferred proposal and scope.
2. Enumerate all docs and references that need updates.
3. Run a single atomic move for directories to preserve history.
4. Update import paths, tooling, and docs links.
5. Run tests to confirm no regressions.
