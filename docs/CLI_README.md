# Cairn CLI

A comprehensive command-line interface for interacting with Cairn workspaces, files, agents, and code providers.

## Installation

The CLI is automatically installed when you install the Cairn package:

```bash
uv sync --all-extras
```

## Usage

The CLI provides the agent lifecycle commands (`up`, `run`, `spawn`,
`queue`, `list-agents`, `status`, `accept`, `reject`, `undo`, `logs`) plus
three command groups:

- `workspace` - Workspace management commands
- `files` - File operations in workspaces
- `preview` - Preview and diff commands

### Getting Help

```bash
# Show main help
cairn --help

# Show help for a specific command group
cairn workspace --help
cairn files --help
cairn --help
cairn preview --help
```

## Workspace Commands

### Create a New Workspace

```bash
cairn workspace create <workspace-name>
```

Example:
```bash
cairn workspace create my-project
```

### List All Workspaces

```bash
cairn workspace list
```

Shows a table of all workspaces with their paths and sizes.

### Show Workspace Information

```bash
cairn workspace info <workspace-name>
```

Example:
```bash
cairn workspace info my-project
```

Displays detailed information including:
- Workspace name and path
- Database size
- Number of files
- Total file size
- Number of KV entries

### Delete a Workspace

```bash
cairn workspace delete <workspace-name>

# Skip confirmation prompt
cairn workspace delete <workspace-name> --force
```

## File Commands

### List Files

```bash
# List files in root directory
cairn files list <workspace-name>

# List files in a specific path
cairn files list <workspace-name> --path /src

# List files recursively
cairn files list <workspace-name> --path /src --recursive
```

### Read a File

```bash
# Read a text file
cairn files read <workspace-name> <file-path>

# Read a binary file
cairn files read <workspace-name> <file-path> --binary
```

Example:
```bash
cairn files read my-project /README.md
```

### Write a File

```bash
# Write a text file
cairn files write <workspace-name> <file-path> <content>

# Write a binary file
cairn files write <workspace-name> <file-path> <content> --binary
```

Example:
```bash
cairn files write my-project /hello.txt "Hello, World!"
```

### Search Files

```bash
cairn files search <workspace-name> <pattern>
```

Example:
```bash
# Find all Python files
cairn files search my-project "**/*.py"

# Find all markdown files
cairn files search my-project "**/*.md"
```

### Show Directory Tree

```bash
# Show full tree
cairn files tree <workspace-name>

# Show tree from a specific path
cairn files tree <workspace-name> --path /src

# Limit tree depth
cairn files tree <workspace-name> --max-depth 2
```

## Agent Commands

### List All Agents

```bash
cairn list
```

Shows a table of all active agents with their states, tasks, and priorities.

### Show Agent Status

```bash
cairn status <agent-id>
```

Example:
```bash
cairn status agent-abc123
```

### Spawn a High-Priority Task

```bash
cairn spawn "<reference>"
```

> The code provider is chosen when the daemon starts (`cairn up
> --provider <name>`) or for inline runs (`cairn run --provider <name>`); the
> daemon's provider resolves every reference.

Examples:
```bash
# With file provider (default)
cairn spawn "scripts/add_docstrings.py"
```

### Queue a Normal-Priority Task

```bash
cairn queue "<reference>"
```

Examples:
```bash
# With file provider (default)
cairn queue "scripts/refactor_tests.py"
```

**Note:** The `reference` argument is interpreted by the code provider:
- `FileCodeProvider` (default): path to a Python script file
- `GitCodeProvider`: git URL with path
- `RegistryCodeProvider`: registry URL

### Accept Agent Changes

```bash
cairn accept <agent-id>
```

Accepts and applies the agent's computed changeset to the actual working tree.

### Reject Agent Changes

```bash
cairn reject <agent-id>
```

Rejects and discards the agent's changes.

## Preview Commands

### Preview Agent Changes

```bash
cairn preview changes <agent-id>
```

Shows a detailed diff of all changes made by an agent, including:
- Change type (added/modified/deleted)
- File paths
- Old and new file sizes

### Preview a Specific File

```bash
cairn preview file <agent-id> <file-path>
```

Example:
```bash
cairn preview file agent-abc123 /src/main.py
```

Shows the content of a specific file from the agent's workspace.

## Global Options

All commands support the following global options:

- `--project-root <path>` - Override the project root directory
- `--cairn-home <path>` - Override the Cairn home directory

Example:
```bash
cairn workspace list --project-root /path/to/project --cairn-home ~/.my-cairn
```

## Common Workflows

### Creating and Populating a Workspace

```bash
# Create a new workspace
cairn workspace create my-workspace

# Write some files
cairn files write my-workspace /README.md "# My Project"
cairn files write my-workspace /src/main.py "def main(): pass"

# Verify the files
cairn files tree my-workspace

# Get workspace info
cairn workspace info my-workspace
```

### Working with Agents

```bash
# Spawn an agent task
cairn spawn "Add type hints to all functions"

# List agents to get the agent ID
cairn list

# Check agent status
cairn status agent-<id>

# Preview changes when agent is done
cairn preview changes agent-<id>

# Accept the changes if they look good
cairn accept agent-<id>
```

### Exploring a Workspace

```bash
# List all workspaces
cairn workspace list

# Show workspace details
cairn workspace info my

# List files
cairn files list my --recursive

# Search for specific files
cairn files search my "**/*.py"

# Show directory tree
cairn files tree my --max-depth 3
```

## Features

- **Rich Terminal Output**: Uses Rich library for beautiful, formatted tables and panels
- **Async Support**: All file and workspace operations use async/await for better performance
- **Error Handling**: Clear error messages with helpful context
- **Type Safety**: Built with Typer for excellent type checking and autocomplete
- **Comprehensive**: Covers all major Cairn operations - workspaces, files, agents, and previews

## Architecture

The CLI is built on:
- **Typer**: Modern CLI framework with excellent UX
- **Rich**: Beautiful terminal formatting
- **FSdantic**: Type-safe workspace and file operations
- **Cairn Orchestrator**: Task orchestration and lifecycle management
- **Code Providers**: Pluggable code sourcing (file, LLM, git, registry)

## Comparison with Original CLI

The Typer CLI (`cairn`) is a complementary interface to the original argparse-based CLI (`cairn`):

| Feature | `cairn` (original) | `cairn` (new) |
|---------|-------------------|-------------------|
| Primary use case | Running orchestrator service | Interactive workspace/file management |
| Agent operations | ✓ | ✓ |
| Workspace management | Limited | ✓ Full CRUD |
| File operations | Through agent tools | ✓ Direct access |
| Preview/diff | Limited | ✓ Rich formatting |
| Output format | Plain text | Rich tables/panels |
| Long-running service | ✓ | ✗ |

Use `cairn up` for running the orchestrator service, and `cairn` for interactive workspace and file management.
