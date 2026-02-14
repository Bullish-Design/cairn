# Neovim Refactoring Plan: Extracting cairn-nvim

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Proposal

## Executive Summary

This document outlines a plan to extract the Neovim plugin from the core Cairn orchestration library into a separate `cairn-nvim` library. The refactoring is **architecturally straightforward** because the Neovim plugin already communicates with Cairn exclusively through a well-defined file-based protocol, with zero Python-Lua coupling.

## Current State Analysis

### Architecture Overview

The Cairn library currently consists of:

1. **Core Orchestrator** (`src/cairn/*.py`) - Python-based agent orchestration, execution, and lifecycle management
2. **Neovim Plugin** (`src/cairn/nvim/`) - Lua-based editor integration for task management and preview

### Critical Finding: Clean Separation Already Exists

**There are ZERO references to "nvim" in any Python files.** The integration is entirely file-based:

```python
# Verified via: grep -r "nvim" src/cairn/*.py
# Result: No matches found
```

### Communication Protocol

The Neovim plugin interacts with Cairn through a **filesystem-based protocol**:

| Protocol Component | Path | Purpose |
|-------------------|------|---------|
| **Signal files** | `~/.cairn/signals/` | Command dispatch (accept, reject) |
| **Queue files** | `~/.cairn/queue/tasks.json` | Task submission |
| **State files** | `~/.cairn/state/` | Agent status (`latest_agent`, `active_agents.json`) |
| **Workspace directories** | `~/.cairn/workspaces/{agent-id}/` | Materialized previews |
| **Preview diffs** | `~/.cairn/previews/{agent-id}.diff` | Change summaries for ghost text |

### Current File Structure

```
src/cairn/
├── nvim/                      # TO BE EXTRACTED
│   ├── plugin/
│   │   └── cairn.lua          # Command registration
│   ├── lua/cairn/
│   │   ├── init.lua           # Setup and keymaps
│   │   ├── commands.lua       # Queue, accept, reject, preview
│   │   ├── config.lua         # Configuration management
│   │   ├── watcher.lua        # File system event watching
│   │   ├── tmux.lua           # TMUX preview integration
│   │   └── ghost.lua          # Ghost text rendering
│   ├── doc/
│   │   └── cairn.txt          # Help documentation
│   └── tests/
│       ├── commands_spec.lua
│       ├── config_spec.lua
│       ├── tmux_spec.lua
│       ├── watcher_spec.lua
│       ├── ghost_spec.lua
│       └── minimal_init.lua
│
├── orchestrator.py            # STAYS - Core orchestrator
├── agent.py                   # STAYS - Agent state models
├── executor.py                # STAYS - Monty sandbox
├── workspace.py               # STAYS - Preview materialization
├── signals.py                 # STAYS - Signal file parsing
└── ... (other Python modules) # STAYS
```

### Key Dependencies

**Neovim Plugin Dependencies (Lua):**
- Neovim 0.7+ (API for extmarks, timers, keymaps)
- TMUX (for preview window management)
- File system access to `$CAIRN_HOME`

**Core Cairn Dependencies (Python):**
- agentfs-sdk
- pydantic-monty
- llm library
- watchfiles
- pydantic-settings

**Zero overlap** between Neovim plugin dependencies and core Cairn dependencies.

## Refactoring Benefits

### 1. Separation of Concerns
- **Core Cairn**: Pure orchestration logic, no editor assumptions
- **cairn-nvim**: Pure editor integration, no orchestration logic

### 2. Independent Development Velocity
- Neovim plugin can release updates without core Cairn changes
- Core Cairn can refactor internals without breaking editor integration (as long as file protocol is maintained)

### 3. Ecosystem Expansion
- Opens door for `cairn-vscode`, `cairn-emacs`, `cairn-helix` using the same protocol
- Each editor plugin can be maintained by its own community

### 4. Reduced Core Library Size
- Core Cairn becomes a pure Python library
- Neovim plugin users only install what they need
- Non-Neovim users don't carry dead code

### 5. Clear API Surface
- File-based protocol becomes the **canonical public API** for editor integrations
- Forces documentation of the protocol contract
- Makes testing easier (just create/read files)

## Refactoring Plan

### Option 1: Minimal Extraction (Recommended)

**Goal:** Move Neovim plugin to separate repository with minimal changes.

**Steps:**

1. **Create new repository: `cairn-nvim`**
   ```
   cairn-nvim/
   ├── plugin/
   │   └── cairn.lua
   ├── lua/cairn/
   │   ├── init.lua
   │   ├── commands.lua
   │   ├── config.lua
   │   ├── watcher.lua
   │   ├── tmux.lua
   │   └── ghost.lua
   ├── doc/
   │   └── cairn.txt
   ├── tests/
   │   └── ... (all spec files)
   ├── README.md
   ├── PROTOCOL.md          # NEW: Document file-based protocol
   └── LICENSE
   ```

2. **Document the protocol in core Cairn**
   - Create `PROTOCOL.md` in Cairn repository
   - Specify file formats, paths, state transitions
   - Declare this as the stable public API

3. **Update Cairn documentation**
   - Remove Neovim-specific setup from main README
   - Add link to cairn-nvim repository
   - Create "Editor Integrations" section

4. **Archive in-tree plugin**
   - Move `src/cairn/nvim/` to `.context/nvim/` (archived)
   - Update TESTING.md to remove Neovim test instructions
   - Add deprecation notice pointing to cairn-nvim

5. **Publish cairn-nvim**
   - Publish to GitHub as standalone repository
   - Add to Neovim plugin registries (lazy.nvim, packer, etc.)
   - Create installation instructions

**Timeline:** 1-2 days
**Breaking Changes:** Neovim plugin path changes (users must update their plugin manager config)
**Risk:** Low (no logic changes, only file moves)

---

### Option 2: Protocol Formalization + Extraction

**Goal:** Formalize file-based protocol first, then extract plugin.

**Additional Steps Beyond Option 1:**

1. **Create `cairn protocol` CLI command**
   ```bash
   # Validate protocol files
   cairn protocol validate

   # Show current agent state
   cairn protocol status

   # Watch protocol events (debug tool)
   cairn protocol watch
   ```

2. **Add protocol version to state files**
   ```json
   {
     "protocol_version": "1.0",
     "agents": { ... }
   }
   ```

3. **Create protocol test suite**
   - Integration tests that validate protocol contracts
   - Can be run by any editor plugin implementation
   - Ensures protocol compatibility across versions

4. **Document protocol extension points**
   - How to add new commands
   - How to extend state files
   - Backward compatibility guarantees

**Timeline:** 3-5 days
**Breaking Changes:** Same as Option 1
**Risk:** Low-Medium (adds validation logic)

---

### Option 3: Multi-Editor Support Framework

**Goal:** Extract Neovim plugin AND create framework for other editors.

**Additional Steps Beyond Option 2:**

1. **Create `cairn-editor-protocol` specification**
   - Separate document defining protocol independent of implementation
   - JSON schemas for all protocol files
   - State machine diagrams for agent lifecycle
   - Example implementations in multiple languages

2. **Create reference editor integrations**
   - `cairn-nvim` (Lua)
   - `cairn-vscode` (TypeScript) - minimal implementation
   - `cairn-cli-tui` (Python/Rich) - terminal UI reference

3. **Add protocol compliance testing**
   ```bash
   # Run against any editor implementation
   cairn protocol test-compliance --editor-command "nvim --headless ..."
   ```

4. **Create editor integration guide**
   - How to implement the protocol in any editor
   - Common patterns (polling vs watching)
   - Best practices for UX

**Timeline:** 1-2 weeks
**Breaking Changes:** Same as Option 1
**Risk:** Medium (more scope, more moving parts)

---

## Protocol Specification (Current State)

### File Formats

#### 1. Queue File (`~/.cairn/queue/tasks.json`)

```json
[
  {
    "task": "Add docstrings to public functions",
    "priority": "NORMAL",  // or "HIGH"
    "created_at": 1707928800
  }
]
```

**Behavior:**
- Orchestrator polls this file (if signal polling enabled) or processes on explicit `cairn queue` CLI call
- Orchestrator dequeues tasks and spawns agents
- File is updated with remaining tasks after dequeue

#### 2. Signal Files (`~/.cairn/signals/`)

**Accept Signal:**
```
~/.cairn/signals/accept-{agent-id}
```
Content: timestamp (Unix epoch)

**Reject Signal:**
```
~/.cairn/signals/reject-{agent-id}
```
Content: timestamp (Unix epoch)

**Behavior:**
- Orchestrator watches for signal file creation
- On detection, processes command and deletes signal file
- Idempotent: multiple signals for same agent are safe

#### 3. State Files (`~/.cairn/state/`)

**Latest Agent (`latest_agent`):**
```
agent-abc123def
```

**Active Agents (`active_agents.json`):**
```json
{
  "agent-abc123": {
    "state": "REVIEWING",
    "task": "Add docstrings",
    "created_at": 1707928800
  },
  "agent-def456": {
    "state": "EXECUTING",
    "task": "Fix bug in parser",
    "created_at": 1707928900
  }
}
```

**Behavior:**
- Written by orchestrator on every state transition
- Read by editor plugins to show agent status
- `latest_agent` updated when agent transitions to REVIEWING state

#### 4. Workspace Directories (`~/.cairn/workspaces/{agent-id}/`)

**Structure:**
```
~/.cairn/workspaces/agent-abc123/
├── src/
│   └── module.py         # Modified by agent
└── tests/
    └── test_module.py    # Modified by agent
```

**Behavior:**
- Created by `workspace.materialize_workspace()` when agent enters REVIEWING state
- Contains full agent overlay state (all modified files)
- Used by editor for preview (open in TMUX pane, etc.)

#### 5. Preview Diffs (`~/.cairn/previews/{agent-id}.diff`)

**Format:** Unified diff format
```diff
--- a/src/module.py
+++ b/src/module.py
@@ -10,6 +10,9 @@ def process():
+    """Process data and return result."""
     data = load_data()
     return transform(data)
```

**Behavior:**
- Generated by orchestrator when agent submits results
- Parsed by editor plugins for ghost text rendering
- Deleted when agent is accepted/rejected

### Protocol Guarantees

1. **Atomicity:** Signal files are atomic (single write)
2. **Idempotency:** Processing same signal multiple times is safe
3. **Eventually Consistent:** State files reflect orchestrator state within 500ms
4. **Fallthrough:** Missing state files are treated as "no active agents"

## Core Cairn Simplifications (Post-Extraction)

### Files to Remove

1. `src/cairn/nvim/` (entire directory)
2. Neovim test instructions in `TESTING.md`
3. Neovim setup instructions in `README.md`

### Documentation to Update

1. **README.md**
   - Remove "Neovim plugin quick setup" section
   - Add "Editor Integrations" section with links
   - Focus on CLI usage

2. **SPEC.md**
   - Add "Protocol Specification" section
   - Document file formats and guarantees
   - Specify protocol versioning strategy

3. **New File: PROTOCOL.md**
   - Full protocol specification
   - Example implementations
   - Protocol versioning and compatibility

### Code Simplifications

**No Python code changes required.** The core library already has zero coupling to Neovim.

However, optional improvements:

1. **Explicit protocol validation**
   ```python
   # In signals.py
   def validate_protocol_version(state_file: Path) -> bool:
       """Ensure state file matches current protocol version."""
       data = json.loads(state_file.read_text())
       return data.get("protocol_version") == CURRENT_PROTOCOL_VERSION
   ```

2. **Protocol documentation generation**
   ```python
   # New module: protocol.py
   def generate_schema() -> dict:
       """Generate JSON schema for protocol files."""
       return {
           "queue": QueueFileSchema.model_json_schema(),
           "state": StateFileSchema.model_json_schema(),
       }
   ```

3. **CLI protocol introspection**
   ```bash
   cairn protocol schema      # Print JSON schemas
   cairn protocol validate    # Validate protocol files
   cairn protocol version     # Show protocol version
   ```

## Migration Guide for Users

### Before (Monolithic)

```lua
-- lazy.nvim config
{
  dir = '~/code/cairn/src/cairn/nvim',
  config = function()
    require('cairn').setup({
      preview_same_location = true,
    })
  end,
}
```

### After (Extracted)

```lua
-- lazy.nvim config
{
  'your-org/cairn-nvim',
  config = function()
    require('cairn').setup({
      preview_same_location = true,
    })
  end,
}
```

**Changes:**
- Plugin source changes from local path to GitHub repo
- All commands, keymaps, and functionality remain identical
- Configuration API stays the same

## Testing Strategy

### Pre-Extraction Tests

1. **Run all current Neovim tests**
   ```bash
   # Verify baseline functionality
   cd src/cairn/nvim/tests
   nvim --headless -c "PlenaryBustedDirectory ."
   ```

2. **Document all Neovim commands and behavior**
   - List all `:Cairn*` commands
   - Test each command manually
   - Document expected file operations

### Post-Extraction Tests

1. **Run tests in new cairn-nvim repository**
   ```bash
   cd cairn-nvim
   nvim --headless -c "PlenaryBustedDirectory tests/"
   ```

2. **Integration test with core Cairn**
   ```bash
   # Start Cairn orchestrator
   cairn up

   # Open Neovim with cairn-nvim plugin
   nvim

   # Execute all commands
   :CairnQueue "test task"
   :CairnAccept
   :CairnReject
   :CairnPreview
   ```

3. **Protocol compliance test**
   - Manually create signal files and verify orchestrator response
   - Manually modify state files and verify plugin response
   - Test error conditions (missing files, malformed JSON, etc.)

## Risks and Mitigations

### Risk 1: Breaking User Installations

**Impact:** High
**Probability:** High
**Mitigation:**
- Provide clear migration guide
- Keep old plugin directory with deprecation notice for 1 release cycle
- Announce breaking change prominently in release notes

### Risk 2: Protocol Drift

**Impact:** High
**Probability:** Medium
**Mitigation:**
- Document protocol explicitly in PROTOCOL.md
- Add protocol version to state files
- Create protocol test suite
- Maintain backward compatibility for at least 2 versions

### Risk 3: Documentation Becomes Outdated

**Impact:** Medium
**Probability:** Medium
**Mitigation:**
- Single source of truth for protocol (PROTOCOL.md in core Cairn repo)
- cairn-nvim documentation links to core protocol spec
- CI tests validate protocol compliance

### Risk 4: Maintenance Burden of Multiple Repositories

**Impact:** Medium
**Probability:** Low
**Mitigation:**
- Neovim plugin is feature-complete (no active development needed)
- Protocol is stable (minimal changes expected)
- Clear ownership: core maintainers own protocol, plugin maintainers own implementation

## Recommendations

### Recommended Approach: **Option 1** (Minimal Extraction)

**Rationale:**
1. The architectural separation already exists
2. File-based protocol is already well-defined in practice
3. Minimal risk, maximum value
4. Can be completed in 1-2 days

**Follow-up Work (Optional):**
- Formalize protocol documentation (Option 2) as separate effort
- Multi-editor support (Option 3) can happen organically over time

### Implementation Steps

**Phase 1: Preparation (Day 1)**
1. Create `PROTOCOL.md` in core Cairn repository
2. Document all file formats, paths, and behaviors
3. Add "Editor Integrations" section to README.md
4. Create new GitHub repository: `cairn-nvim`

**Phase 2: Extraction (Day 1-2)**
1. Copy `src/cairn/nvim/` to `cairn-nvim` repository
2. Add README.md, LICENSE to cairn-nvim
3. Set up CI for cairn-nvim tests
4. Publish to GitHub

**Phase 3: Cleanup (Day 2)**
1. Move `src/cairn/nvim/` to `.context/nvim/` in core repository
2. Update TESTING.md to remove Neovim test instructions
3. Add deprecation notice to in-tree plugin
4. Update main README to link to cairn-nvim

**Phase 4: Release (Day 2)**
1. Tag new release of core Cairn with breaking change notice
2. Announce cairn-nvim repository
3. Update installation documentation
4. Notify users in release notes

## Future Possibilities

### Other Editor Integrations

**VSCode Extension (`cairn-vscode`):**
- TreeView for active agents
- CodeLens for ghost text
- Command palette integration
- Status bar widget

**Emacs Package (`cairn.el`):**
- Minor mode for Cairn integration
- Ivy/Helm integration for agent selection
- Magit-style accept/reject interface
- Org-mode task integration

**Helix Plugin (`cairn-helix`):**
- LSP-style integration
- Picker for agent selection
- Preview in split

### Protocol Extensions

**WebSocket Protocol (Future):**
- Real-time updates instead of polling
- Lower latency for ghost text
- Better multi-editor support
- Requires orchestrator enhancement

**RPC Protocol (Future):**
- Direct function calls instead of signal files
- Type-safe protocol with msgpack/JSON-RPC
- Better error handling
- Requires major orchestrator refactoring

## Conclusion

The Neovim plugin refactoring is **architecturally straightforward** because the separation already exists. The current file-based protocol is clean, well-defined, and requires no changes to core Cairn Python code.

**Recommended Action:** Proceed with **Option 1** (Minimal Extraction) to:
1. Reduce core library scope
2. Enable independent plugin development
3. Open door for other editor integrations
4. Maintain all current functionality with minimal risk

The refactoring can be completed in **1-2 days** with low risk and high value.

---

**Next Steps:**
1. Review this proposal with maintainers
2. Get approval for breaking change
3. Create cairn-nvim repository
4. Execute Phase 1-4 implementation plan
5. Announce and document migration path
