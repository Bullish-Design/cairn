# Cairn CLI

A comprehensive command-line interface for interacting with Cairn workspaces, files, agents, and code providers.

## Installation

The CLI is automatically installed when you install the Cairn package:

```bash
uv sync --all-extras
```

## Usage

The CLI provides four main command groups:

- `workspace` - Workspace management commands
- `files` - File operations in workspaces
- `agent` - Agent management commands
- `preview` - Preview and diff commands

### Getting Help

```bash
# Show main help
cairn-cli --help

# Show help for a specific command group
cairn-cli workspace --help
cairn-cli files --help
cairn-cli agent --help
cairn-cli preview --help
```

## Workspace Commands

### Create a New Workspace

```bash
cairn-cli workspace create <workspace-name>
```

Example:
```bash
cairn-cli workspace create my-project
```

### List All Workspaces

```bash
cairn-cli workspace list
```

Shows a table of all workspaces with their paths and sizes.

### Show Workspace Information

```bash
cairn-cli workspace info <workspace-name>
```

Example:
```bash
cairn-cli workspace info my-project
```

Displays detailed information including:
- Workspace name and path
- Database size
- Number of files
- Total file size
- Number of KV entries

### Delete a Workspace

```bash
cairn-cli workspace delete <workspace-name>

# Skip confirmation prompt
cairn-cli workspace delete <workspace-name> --force
```

## File Commands

### List Files

```bash
# List files in root directory
cairn-cli files list <workspace-name>

# List files in a specific path
cairn-cli files list <workspace-name> --path /src

# List files recursively
cairn-cli files list <workspace-name> --path /src --recursive
```

### Read a File

```bash
# Read a text file
cairn-cli files read <workspace-name> <file-path>

# Read a binary file
cairn-cli files read <workspace-name> <file-path> --binary
```

Example:
```bash
cairn-cli files read my-project /README.md
```

### Write a File

```bash
# Write a text file
cairn-cli files write <workspace-name> <file-path> <content>

# Write a binary file
cairn-cli files write <workspace-name> <file-path> <content> --binary
```

Example:
```bash
cairn-cli files write my-project /hello.txt "Hello, World!"
```

### Search Files

```bash
cairn-cli files search <workspace-name> <pattern>
```

Example:
```bash
# Find all Python files
cairn-cli files search my-project "**/*.py"

# Find all markdown files
cairn-cli files search my-project "**/*.md"
```

### Show Directory Tree

```bash
# Show full tree
cairn-cli files tree <workspace-name>

# Show tree from a specific path
cairn-cli files tree <workspace-name> --path /src

# Limit tree depth
cairn-cli files tree <workspace-name> --max-depth 2
```

## Agent Commands

### List All Agents

```bash
cairn-cli agent list
```

Shows a table of all active agents with their states, tasks, and priorities.

### Show Agent Status

```bash
cairn-cli agent status <agent-id>
```

Example:
```bash
cairn-cli agent status agent-abc123
```

### Spawn a High-Priority Task

```bash
cairn-cli agent spawn "<reference>" [--provider PROVIDER]
```

Examples:
```bash
# With file provider (default)
cairn-cli agent spawn "scripts/add_docstrings.pym"

# With LLM provider (requires cairn-llm plugin)
cairn-cli agent spawn "Add docstrings to all public functions" --provider llm
```

### Queue a Normal-Priority Task

```bash
cairn-cli agent queue "<reference>" [--provider PROVIDER]
```

Examples:
```bash
# With file provider (default)
cairn-cli agent queue "scripts/refactor_tests.pym"

# With LLM provider (requires cairn-llm plugin)
cairn-cli agent queue "Refactor test suite" --provider llm
```

**Note:** The `reference` argument is interpreted by the code provider:
- `FileCodeProvider` (default): path to a `.pym` file
- `LLMCodeProvider` (--provider llm): natural language task description
- `GitCodeProvider`: git URL with path
- `RegistryCodeProvider`: registry URL

### Accept Agent Changes

```bash
cairn-cli agent accept <agent-id>
```

Accepts and merges the agent's changes into the stable workspace.

### Reject Agent Changes

```bash
cairn-cli agent reject <agent-id>
```

Rejects and discards the agent's changes.

## Preview Commands

### Preview Agent Changes

```bash
cairn-cli preview changes <agent-id>
```

Shows a detailed diff of all changes made by an agent, including:
- Change type (added/modified/deleted)
- File paths
- Old and new file sizes

### Preview a Specific File

```bash
cairn-cli preview file <agent-id> <file-path>
```

Example:
```bash
cairn-cli preview file agent-abc123 /src/main.py
```

Shows the content of a specific file from the agent's workspace.

## Global Options

All commands support the following global options:

- `--project-root <path>` - Override the project root directory
- `--cairn-home <path>` - Override the Cairn home directory

Example:
```bash
cairn-cli workspace list --project-root /path/to/project --cairn-home ~/.my-cairn
```

## Common Workflows

### Creating and Populating a Workspace

```bash
# Create a new workspace
cairn-cli workspace create my-workspace

# Write some files
cairn-cli files write my-workspace /README.md "# My Project"
cairn-cli files write my-workspace /src/main.py "def main(): pass"

# Verify the files
cairn-cli files tree my-workspace

# Get workspace info
cairn-cli workspace info my-workspace
```

### Working with Agents

```bash
# Spawn an agent task
cairn-cli agent spawn "Add type hints to all functions"

# List agents to get the agent ID
cairn-cli agent list

# Check agent status
cairn-cli agent status agent-<id>

# Preview changes when agent is done
cairn-cli preview changes agent-<id>

# Accept the changes if they look good
cairn-cli agent accept agent-<id>
```

### Exploring a Workspace

```bash
# List all workspaces
cairn-cli workspace list

# Show workspace details
cairn-cli workspace info stable

# List files
cairn-cli files list stable --recursive

# Search for specific files
cairn-cli files search stable "**/*.py"

# Show directory tree
cairn-cli files tree stable --max-depth 3
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

The Typer CLI (`cairn-cli`) is a complementary interface to the original argparse-based CLI (`cairn`):

| Feature | `cairn` (original) | `cairn-cli` (new) |
|---------|-------------------|-------------------|
| Primary use case | Running orchestrator service | Interactive workspace/file management |
| Agent operations | ✓ | ✓ |
| Workspace management | Limited | ✓ Full CRUD |
| File operations | Through agent tools | ✓ Direct access |
| Preview/diff | Limited | ✓ Rich formatting |
| Output format | Plain text | Rich tables/panels |
| Long-running service | ✓ | ✗ |

Use `cairn up` for running the orchestrator service, and `cairn-cli` for interactive workspace and file management.
