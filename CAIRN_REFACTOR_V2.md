# Cairn Library Refactoring Plan V2

**Date:** 2026-02-16
**Status:** Draft
**Purpose:** Complete architectural refactor of Cairn as a general-purpose sandboxed code orchestrator with pluggable code providers

---

## Executive Summary

Cairn is being refactored from a specialized "AI agent orchestration library" into a **general-purpose sandboxed code orchestration runtime** with pluggable code providers. This transforms Cairn from an AI-specific tool into a foundational library for any use case requiring isolated, human-controlled code execution.

**Key Architectural Changes:**

1. **Extract LLM Code Generation** → Move to separate `cairn-llm` plugin
2. **Add Code Provider Abstraction** → Pluggable interface for code sourcing
3. **Leverage New FSdantic API** → Workspace managers (files, kv, overlay, materialize)
4. **Leverage New Grail API** → `.pym` file-based execution with pre-flight validation
5. **Simplify Core Orchestrator** → Focus on workspace + execution + human control
6. **Enable Plugin Ecosystem** → File, inline, LLM, Git, registry providers

**Core Concept Transformation:**

**Before (V1):**
> Cairn is an orchestration runtime for AI code agents with isolated fsdantic workspaces and explicit human accept/reject control.

**After (V2):**
> Cairn is a workspace-aware orchestration runtime for sandboxed code execution with copy-on-write isolation and explicit human integration control.

**Benefits:**

- **General-purpose**: Not limited to AI agents
- **Simpler core**: No LLM dependencies in core library
- **Pluggable**: Multiple code sources (files, LLM, git, registry)
- **Better testability**: Deterministic file-based testing
- **Cleaner boundaries**: Clear separation between orchestration and code sourcing
- **Broader use cases**: Untrusted code execution, preview environments, CI/CD

---

## Table of Contents

1. [Vision Change: From AI-Specific to General-Purpose](#1-vision-change-from-ai-specific-to-general-purpose)
2. [Current State Analysis](#2-current-state-analysis)
3. [FSdantic Integration Changes](#3-fsdantic-integration-changes)
4. [Grail Integration Changes](#4-grail-integration-changes)
5. [Code Provider Architecture](#5-code-provider-architecture)
6. [Orchestrator Refactoring](#6-orchestrator-refactoring)
7. [File Structure Changes](#7-file-structure-changes)
8. [Plugin Ecosystem](#8-plugin-ecosystem)
9. [Migration Path](#9-migration-path)
10. [Testing Strategy](#10-testing-strategy)
11. [Implementation Phases](#11-implementation-phases)
12. [Benefits Summary](#12-benefits-summary)

---

## 1. Vision Change: From AI-Specific to General-Purpose

### 1.1 The Conceptual Shift

**Old Vision (AI-Centric):**
```
Natural Language Task → LLM → Code → Sandbox → Human Review → Merge
        ↑                                                         ↓
        └──────────────── Built into Cairn Core ────────────────┘
```

**New Vision (Code-Centric):**
```
Code Reference → [Provider Plugin] → Code → Sandbox → Human Review → Merge
     ↑                                                                ↓
User Input      External Concern         ←─── Core Cairn ────────────┘
```

### 1.2 What Cairn Is Now About

**Core Responsibilities:**
1. ✅ **Workspace Isolation** - Execute code in copy-on-write overlays
2. ✅ **Sandboxed Execution** - Run code in Monty with no direct system access
3. ✅ **Human Authority** - Accept/reject as integration gate
4. ✅ **Materialized Preview** - Inspect changes before merge
5. ✅ **Orchestration** - Queue, concurrency, lifecycle, recovery

**Extracted to Plugins:**
1. ❌ LLM integration and prompting
2. ❌ Natural language → code transformation
3. ❌ Model selection and management
4. ❌ Prompt engineering

### 1.3 New Use Cases Enabled

**1. File-Based Task Execution**
```bash
cairn spawn scripts/refactor_imports.pym
```

**2. Untrusted User Scripts**
```python
# Website accepting user-submitted code
orchestrator = CairnOrchestrator(
    code_provider=UserSubmissionProvider(sandbox_level="strict")
)
await orchestrator.spawn_agent(task=user_code)
```

**3. Preview Environments for Code Changes**
```python
# Test code changes in isolation before merging
orchestrator = CairnOrchestrator(
    code_provider=FileCodeProvider()
)
agent_id = await orchestrator.spawn_agent("scripts/migration.pym")
# Review in preview dir, then accept or reject
```

**4. CI/CD with Sandboxed Execution**
```python
# Run build/test scripts in isolated workspaces
orchestrator = CairnOrchestrator(
    code_provider=GitCodeProvider(repo="github.com/org/scripts")
)
```

**5. LLM Code Generation (via plugin)**
```bash
# With cairn-llm plugin installed
cairn spawn "Add type hints to all functions" --provider llm
```

### 1.4 Core Value Proposition

**Cairn provides:**
- Safe execution of untrusted code
- Isolated workspace management with copy-on-write
- Human-controlled integration (no automatic merges)
- Preview environments for inspection
- Task orchestration with recovery

**Cairn is useful when you need:**
- To run code you don't fully trust
- Isolation between concurrent operations
- Human review before changes go live
- Rollback capability
- Execution state tracking and recovery

---

## 2. Current State Analysis

### 2.1 Current FSdantic Usage Issues

**Problem:** Using deprecated API patterns

```python
# Current (OLD API):
self.stable = await Fsdantic.open_with_options(
    AgentFSOptions(path=str(self.agentfs_dir / "stable.db"))
)

# Uses raw AgentFS instead of workspace managers
self.file_ops = FileOperations(agent_fs.raw, base_fs=stable_fs.raw)
```

**Impact:**
- Not using new workspace manager pattern
- Accessing `.raw` escape hatch unnecessarily
- Missing out on cleaner error handling
- Not leveraging batch APIs or new convenience methods

### 2.2 Current Grail Usage Issues

**Problem:** Using old Grail v1 MontyContext

```python
# Current (OLD API):
from grail import MontyContext

# Code generated as strings
code = await self.llm.generate(task)

# Complex MontyContext setup (13 parameters)
context = MontyContext(
    input_model=EmptyInput,
    tools=tools,
    limits=limits,
    # ... many more parameters
)
```

**Impact:**
- No IDE support for agent code (strings, not files)
- No pre-flight validation
- No inspectable generated code
- Complex setup process
- Errors discovered only at runtime

### 2.3 Code Generation Coupling Issues

**Problem:** LLM code generation is tightly coupled to core orchestrator

```python
# Current approach:
class CairnOrchestrator:
    def __init__(self, code_generator: CodeGenerator | None = None):
        self.llm = code_generator or CodeGenerator()

    async def _run_agent(self, agent_id: str):
        code = await self.llm.generate(task)  # ← Tight coupling
```

**Impact:**
- Cannot use cairn without LLM dependencies
- Cannot easily substitute other code sources
- Testing requires mocking LLM calls
- Core library tied to AI use case
- No clear separation of concerns

### 2.4 Limited Code Sourcing Options

**Problem:** Only one way to provide code (LLM generation)

**Impact:**
- Cannot use pre-written `.pym` files
- Cannot integrate with git repos or registries
- Cannot handle user-submitted code
- Limits use cases to AI code generation only

---

## 3. FSdantic Integration Changes

### 3.1 Replace Workspace Opening

**Before:**
```python
self.stable = await Fsdantic.open_with_options(
    AgentFSOptions(path=str(self.agentfs_dir / "stable.db"))
)
```

**After:**
```python
self.stable = await Fsdantic.open(path=str(self.agentfs_dir / "stable.db"))
```

**Benefits:**
- Simpler API
- Workspace object has managers built-in
- No need for AgentFSOptions wrapper

### 3.2 Use Workspace Managers

**Before:**
```python
# Direct raw access
content = await agent_fs.raw.fs.read_file(path)
await agent_fs.raw.fs.write_file(path, content)

# Manual FileOperations setup
ops = FileOperations(agent_fs.raw, base_fs=stable_fs.raw)
```

**After:**
```python
# Use workspace.files manager
content = await agent_fs.files.read(path)
await agent_fs.files.write(path, content)

# Query support
results = await agent_fs.files.query(
    ViewQuery(path_pattern="**/*.py", include_stats=True)
)

# Built-in search
files = await agent_fs.files.search("**/*.txt")
```

**Benefits:**
- No need for FileOperations wrapper class
- Cleaner API surface
- Better error messages
- Type safety throughout

### 3.3 Use Overlay Manager for Accept/Reject

**Before:**
```python
# Manual overlay manipulation
# (Current implementation likely does manual file copying)
```

**After:**
```python
# Accept agent changes
result = await stable.overlay.merge(
    agent_fs,
    strategy=MergeStrategy.OVERWRITE
)

# Preview changes first
changes = await stable.overlay.list_changes(agent_fs, path="/")

# Reject (reset overlay)
removed = await agent_fs.overlay.reset(paths=["/"])
```

**Benefits:**
- Built-in conflict detection
- Multiple merge strategies
- Clear change tracking
- Safer operations

### 3.4 Use Materialization Manager for Previews

**Before:**
```python
# Manual workspace materialization
# (Current implementation likely copies files manually)
```

**After:**
```python
# Generate preview workspace
preview_dir = self.cairn_home / "previews" / agent_id
result = await agent_fs.materialize.to_disk(
    preview_dir,
    base=stable,
    clean=True,
    allow_root=self.cairn_home / "previews"
)

# Or just compute diff
diff = await agent_fs.materialize.diff(stable)
for change in diff:
    print(f"{change.change_type}: {change.path}")
```

**Benefits:**
- Safe materialization with staging
- Automatic cleanup
- Change tracking
- Error recovery

---

## 4. Grail Integration Changes

### 4.1 Replace MontyContext with grail.load()

**Before:**
```python
from grail import MontyContext

context = MontyContext(
    input_model=EmptyInput,
    tools=tools,
    limits=limits,
    filesystem=...,
    # ... many more parameters
)

result = await context.execute_async(code)
```

**After:**
```python
import grail

# Load .pym file
script = grail.load(f".grail/agents/{agent_id}/task.pym")

# Check for errors
check_result = script.check()
if not check_result.valid:
    # Handle validation errors
    pass

# Run with inputs and externals
result = await script.run(
    inputs={"task_description": task},
    externals=external_functions,
)
```

**Benefits:**
- Simpler API (3 parameters vs 13)
- Pre-flight validation
- Inspectable code in `.pym` file
- Better error messages
- Type checking before execution

### 4.2 Use .pym Files for All Code

**Structure:**
```python
# .grail/agents/{agent_id}/task.pym
from grail import external, Input
from typing import Any

# Inputs
task_description: str = Input("task_description")

# External functions (stubs)
@external
async def read_file(path: str) -> str:
    """Read file from workspace."""
    ...

@external
async def write_file(path: str, content: str) -> bool:
    """Write file to workspace."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the task result."""
    ...

# Task code
files = await search_files("*.py")
for f in files:
    content = await read_file(f)
    # ... process

await submit_result(summary="Done", changed_files=[])

# Return value
{"status": "complete"}
```

**Benefits:**
- Full IDE support (syntax highlighting, autocomplete)
- Type checking
- Inspectable code
- Pre-flight validation
- Debugging support

### 4.3 Use grail check for Validation

```python
# Before running agent
script = grail.load(pym_file)
check_result = script.check()

if not check_result.valid:
    for error in check_result.errors:
        print(f"Line {error.lineno}: {error.code} - {error.message}")
    # Transition to ERRORED state
    return

# Only run if validation passes
result = await script.run(...)
```

---

## 5. Code Provider Architecture

### 5.1 Core Abstraction: CodeProvider Protocol

This is the **key architectural change** in V2.

```python
# src/cairn/providers.py (NEW FILE)
from typing import Protocol, Any, runtime_checkable

@runtime_checkable
class CodeProvider(Protocol):
    """
    Interface for providing executable code to cairn.

    Providers can source code from files, LLMs, git repos,
    registries, or any other source.
    """

    async def get_code(
        self,
        reference: str,
        context: dict[str, Any]
    ) -> str:
        """
        Get executable Python code for a task.

        Args:
            reference: How to locate/generate the code. Interpretation
                      depends on provider:
                      - FileCodeProvider: file path to .pym
                      - LLMCodeProvider: natural language task
                      - GitCodeProvider: git URL with ref
                      - RegistryCodeProvider: registry URL
                      - InlineCodeProvider: the code itself
            context: Additional context (agent_id, workspace, etc.)

        Returns:
            Valid Python code string ready for .pym file

        Raises:
            CodeProviderError: If code cannot be obtained
        """
        ...

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        """
        Optional validation hook.

        Returns:
            (is_valid, error_message)
        """
        return (True, None)  # Default: always valid
```

### 5.2 Built-in Providers (Core Library)

These providers ship with cairn core (no extra dependencies).

#### FileCodeProvider

```python
# src/cairn/providers.py
from pathlib import Path

class FileCodeProvider:
    """Load code from .pym files on disk."""

    def __init__(self, base_path: Path | str | None = None):
        """
        Args:
            base_path: Optional base directory for relative paths
        """
        self.base_path = Path(base_path) if base_path else Path.cwd()

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        """
        Load code from file path.

        Args:
            reference: File path (absolute or relative to base_path)
        """
        path = Path(reference)
        if not path.is_absolute():
            path = self.base_path / path

        if not path.exists():
            raise CodeProviderError(f"Code file not found: {path}")

        if not path.suffix == ".pym":
            raise CodeProviderError(f"Expected .pym file, got: {path.suffix}")

        return path.read_text()


class InlineCodeProvider:
    """Code is provided directly as a string."""

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        """
        reference IS the code itself.
        """
        return reference
```

### 5.3 Plugin Providers (Separate Packages)

These providers live in separate packages with their own dependencies.

#### LLMCodeProvider (cairn-llm package)

```python
# cairn_llm/provider.py
from cairn.providers import CodeProvider
import llm  # Simon Willison's llm library

class LLMCodeProvider:
    """Generate code using LLM from natural language tasks."""

    def __init__(
        self,
        model: str = "gpt-4",
        temperature: float = 0.2,
        template_path: Path | None = None,
    ):
        self.model = llm.get_model(model)
        self.temperature = temperature
        self.template = self._load_template(template_path)

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        """
        Generate code from natural language task.

        Args:
            reference: Natural language task description
            context: May include workspace info, agent_id, etc.
        """
        prompt = self._build_prompt(reference, context)

        response = await self.model.prompt_async(
            prompt,
            temperature=self.temperature
        )

        code = self._extract_code(response.text())
        return code

    def _build_prompt(self, task: str, context: dict[str, Any]) -> str:
        """Build prompt for .pym generation."""
        return self.template.format(
            task=task,
            agent_id=context.get("agent_id", "unknown"),
            # Include external function stubs
            external_stubs=self._get_external_stubs(),
        )

    def _get_external_stubs(self) -> str:
        """Generate @external function stubs for prompt."""
        return '''
@external
async def read_file(path: str) -> str:
    """Read file from workspace."""
    ...

@external
async def write_file(path: str, content: str) -> bool:
    """Write file to workspace."""
    ...

@external
async def search_files(pattern: str) -> list[str]:
    """Search for files by glob pattern."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the task result."""
    ...
'''

    def _extract_code(self, response: str) -> str:
        """Extract code from LLM response."""
        # Handle markdown code blocks
        if "```python" in response:
            start = response.find("```python") + len("```python")
            end = response.find("```", start)
            return response[start:end].strip()
        return response.strip()

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        """Validate generated code structure."""
        required = ["from grail import", "@external", "submit_result"]
        for req in required:
            if req not in code:
                return (False, f"Generated code missing: {req}")
        return (True, None)
```

#### GitCodeProvider (cairn-git package)

```python
# cairn_git/provider.py
from cairn.providers import CodeProvider
import subprocess
from pathlib import Path

class GitCodeProvider:
    """Load code from git repositories."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        default_branch: str = "main",
    ):
        self.cache_dir = cache_dir or Path.home() / ".cairn" / "git-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_branch = default_branch

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        """
        Load code from git repository.

        Args:
            reference: Git URL with optional ref and path
                      Format: "git://github.com/user/repo@ref:path/to/script.pym"
                      Or: "git://github.com/user/repo:path/to/script.pym"
        """
        parsed = self._parse_git_reference(reference)

        # Clone/update repo
        repo_path = await self._ensure_repo(
            parsed["repo_url"],
            parsed["ref"]
        )

        # Read file
        file_path = repo_path / parsed["path"]
        if not file_path.exists():
            raise CodeProviderError(f"File not found in repo: {parsed['path']}")

        return file_path.read_text()

    def _parse_git_reference(self, reference: str) -> dict:
        """Parse git:// URL format."""
        if not reference.startswith("git://"):
            raise CodeProviderError("Git reference must start with git://")

        # Remove git:// prefix
        ref = reference[6:]

        # Split repo and path
        if ":" in ref:
            repo_part, path = ref.rsplit(":", 1)
        else:
            raise CodeProviderError("Git reference missing path (use :path)")

        # Split repo and branch/tag
        if "@" in repo_part:
            repo_url, git_ref = repo_part.rsplit("@", 1)
        else:
            repo_url = repo_part
            git_ref = self.default_branch

        return {
            "repo_url": f"https://{repo_url}",
            "ref": git_ref,
            "path": path,
        }

    async def _ensure_repo(self, repo_url: str, ref: str) -> Path:
        """Clone or update git repository."""
        # Implementation details...
        pass
```

#### RegistryCodeProvider (cairn-registry package)

```python
# cairn_registry/provider.py
from cairn.providers import CodeProvider
import httpx

class RegistryCodeProvider:
    """Load code from a script registry."""

    def __init__(
        self,
        registry_url: str,
        api_key: str | None = None,
    ):
        self.registry_url = registry_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.AsyncClient()

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        """
        Load code from registry.

        Args:
            reference: Registry reference
                      Format: "registry://org/script-name:version"
                      Or: "registry://org/script-name" (latest)
        """
        parsed = self._parse_registry_reference(reference)

        url = f"{self.registry_url}/scripts/{parsed['org']}/{parsed['name']}"
        if parsed["version"]:
            url += f"?version={parsed['version']}"

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = await self.client.get(url, headers=headers)
        response.raise_for_status()

        return response.json()["code"]
```

### 5.4 Provider Selection in Orchestrator

```python
# src/cairn/orchestrator.py (UPDATED)
from cairn.providers import CodeProvider, FileCodeProvider

class CairnOrchestrator:
    def __init__(
        self,
        project_root: Path | str = ".",
        cairn_home: Path | str | None = None,
        config: OrchestratorSettings | None = None,
        executor_settings: ExecutorSettings | None = None,
        code_provider: CodeProvider | None = None,  # ← NEW
        tools_factory: Callable[...] | None = None,
    ):
        # ... setup ...

        # Default to file-based provider
        self.code_provider = code_provider or FileCodeProvider()
        self.tools_factory = tools_factory or create_agent_tools
```

### 5.5 Using Providers in Agent Execution

```python
# src/cairn/orchestrator.py
async def _run_agent(self, agent_id: str) -> None:
    """Execute agent task through complete lifecycle."""
    ctx = self.active_agents.get(agent_id)
    try:
        # GENERATING phase (now provider-based)
        await self._transition_state(ctx, AgentState.GENERATING)

        # Use provider to get code
        code = await self.code_provider.get_code(
            reference=ctx.task,  # Interpretation depends on provider
            context={
                "agent_id": agent_id,
                "workspace": ctx.agent_fs,
                "stable": self.stable,
            }
        )

        # Validate code if provider supports it
        is_valid, error = await self.code_provider.validate_code(code)
        if not is_valid:
            await self._handle_invalid_code(ctx, error)
            return

        # Write to .pym file
        pym_file = await self._write_pym_file(agent_id, code)
        ctx.generated_code = code
        ctx.pym_file = pym_file

        # EXECUTING phase
        await self._transition_state(ctx, AgentState.EXECUTING)
        script = grail.load(str(pym_file))

        # Grail pre-flight check
        check_result = script.check()
        if not check_result.valid:
            await self._handle_validation_errors(ctx, check_result)
            return

        # Run the script
        external_functions = self._create_external_functions(ctx)
        result = await script.run(
            inputs={"task_description": ctx.task},
            externals=external_functions,
        )

        # ... rest of execution flow ...

    except CodeProviderError as e:
        await self._handle_provider_error(ctx, e)
    except grail.ExecutionError as e:
        await self._handle_execution_error(ctx, e)
```

---

## 6. Orchestrator Refactoring

### 6.1 Simplified Agent Execution Flow

```python
async def _execute_agent(self, ctx: AgentContext):
    """Execute agent task through complete lifecycle."""
    try:
        # GENERATING phase (provider-based)
        await self._transition_state(ctx, AgentState.GENERATING)
        code = await self.code_provider.get_code(
            reference=ctx.task,
            context={"agent_id": ctx.agent_id, "workspace": ctx.agent_fs}
        )

        # Validate via provider
        is_valid, error = await self.code_provider.validate_code(code)
        if not is_valid:
            await self._handle_invalid_code(ctx, error)
            return

        # Write .pym file
        pym_file = await self._write_pym_file(ctx.agent_id, code)

        # EXECUTING phase
        await self._transition_state(ctx, AgentState.EXECUTING)
        script = grail.load(str(pym_file))

        # Check before running
        check_result = script.check()
        if not check_result.valid:
            await self._handle_validation_errors(ctx, check_result)
            return

        # Run the script
        external_functions = self._create_external_functions(ctx)
        result = await script.run(
            inputs={"task_description": ctx.task},
            externals=external_functions,
        )

        # SUBMITTING phase
        await self._transition_state(ctx, AgentState.SUBMITTING)
        await self._collect_submission(ctx)

        # REVIEWING phase
        await self._transition_state(ctx, AgentState.REVIEWING)
        await self._materialize_preview(ctx)

    except CodeProviderError as e:
        await self._handle_provider_error(ctx, e)
    except grail.ExecutionError as e:
        await self._handle_execution_error(ctx, e)
    except grail.InputError as e:
        await self._handle_input_error(ctx, e)
    except Exception as e:
        await self._handle_unexpected_error(ctx, e)
```

### 6.2 Simplify External Function Creation

```python
def _create_external_functions(self, ctx: AgentContext) -> dict[str, Callable]:
    """Create external functions for agent execution."""
    agent_fs = ctx.agent_fs
    stable_fs = self.stable

    return {
        "read_file": lambda path: agent_fs.files.read(path),
        "write_file": lambda path, content: agent_fs.files.write(path, content),
        "list_dir": lambda path=".": agent_fs.files.list_dir(path, output="name"),
        "file_exists": lambda path: agent_fs.files.exists(path),
        "search_files": lambda pattern: agent_fs.files.search(pattern),
        "search_content": self._make_search_content_fn(agent_fs, stable_fs),
        "submit_result": self._make_submit_result_fn(ctx),
        "log": lambda msg: self._log_agent_message(ctx.agent_id, msg),
    }

def _make_search_content_fn(self, agent_fs, stable_fs):
    """Create search_content function with fallthrough."""
    async def search_content(pattern: str, path: str = ".") -> list[dict]:
        results = await agent_fs.files.query(
            ViewQuery(
                path_pattern=self._normalize_search_path(path),
                content_regex=pattern,
                recursive=True,
                include_content=True,
            )
        )
        return [
            {"file": r.path, "line": ..., "text": ...}
            for r in results
        ]
    return search_content
```

### 6.3 Simplify Accept/Reject

```python
async def accept_agent(self, agent_id: str):
    """Accept agent changes and merge into stable."""
    ctx = self.active_agents.get(agent_id)
    if not ctx:
        raise KeyError(f"Agent {agent_id} not found")

    if ctx.state != AgentState.REVIEWING:
        raise ValueError(f"Agent {agent_id} not in REVIEWING state")

    # Merge changes into stable using overlay manager
    result = await self.stable.overlay.merge(
        ctx.agent_fs,
        strategy=MergeStrategy.OVERWRITE
    )

    if result.errors:
        raise RuntimeError(f"Merge failed: {result.errors}")

    # Transition to ACCEPTED
    await self._transition_state(ctx, AgentState.ACCEPTED)
    await self._cleanup_agent(ctx)

async def reject_agent(self, agent_id: str):
    """Reject agent changes and discard overlay."""
    ctx = self.active_agents.get(agent_id)
    if not ctx:
        raise KeyError(f"Agent {agent_id} not found")

    if ctx.state != AgentState.REVIEWING:
        raise ValueError(f"Agent {agent_id} not in REVIEWING state")

    # Just transition and cleanup (overlay is discarded)
    await self._transition_state(ctx, AgentState.REJECTED)
    await self._cleanup_agent(ctx)
```

---

## 7. File Structure Changes

### 7.1 New Directory Structure

```
cairn/
├── src/cairn/
│   ├── __init__.py
│   ├── orchestrator.py       # Simplified orchestrator (provider-based)
│   ├── providers.py           # NEW: CodeProvider protocol + built-in providers
│   ├── agent.py               # Agent models (minimal changes)
│   ├── external_functions.py  # External function factory
│   ├── lifecycle.py           # Lifecycle management
│   ├── queue.py               # Task queue
│   ├── commands.py            # Command models
│   ├── settings.py            # Settings
│   ├── signals.py             # Signal handling
│   ├── watcher.py             # File watching
│   └── cli.py                 # CLI (updated for --provider flag)
│
├── .grail/                    # Grail artifacts directory
│   └── agents/
│       └── {agent_id}/
│           ├── task.pym       # Generated/loaded agent code
│           ├── stubs.pyi      # Generated stubs
│           ├── check.json     # Validation results
│           └── run.log
│
├── .agentfs/                  # AgentFS databases
│   ├── stable.db
│   ├── bin.db
│   └── agent-{id}.db
│
└── $CAIRN_HOME/               # ~/.cairn
    ├── previews/
    │   └── {agent_id}/        # Materialized previews
    ├── signals/
    └── state/
```

### 7.2 Plugin Package Structures

```
# cairn-llm plugin
cairn-llm/
├── cairn_llm/
│   ├── __init__.py
│   ├── provider.py            # LLMCodeProvider
│   ├── prompts/
│   │   └── default.txt        # Default prompt template
│   └── cli.py                 # CLI extensions (optional)
├── pyproject.toml
└── README.md

# cairn-git plugin
cairn-git/
├── cairn_git/
│   ├── __init__.py
│   ├── provider.py            # GitCodeProvider
│   └── cache.py               # Git caching logic
├── pyproject.toml
└── README.md

# cairn-registry plugin
cairn-registry/
├── cairn_registry/
│   ├── __init__.py
│   ├── provider.py            # RegistryCodeProvider
│   └── client.py              # Registry API client
├── pyproject.toml
└── README.md
```

### 7.3 Removed Files

- `code_generator.py` - Moved to cairn-llm plugin
- `agent_tools.py` - Replaced by external_functions.py
- Any old Grail v1 integration code

### 7.4 New Files

- `providers.py` - CodeProvider protocol and built-in providers
- `external_functions.py` - External function factory

---

## 8. Plugin Ecosystem

### 8.1 Core Package: `cairn`

**Installation:**
```bash
pip install cairn
```

**Dependencies:**
```toml
# pyproject.toml
[project]
dependencies = [
    "fsdantic>=0.3.0",
    "grail>=2.0.0",
    "pydantic>=2.0.0",
    # NO LLM dependencies
]
```

**What's included:**
- Orchestrator runtime
- Workspace management
- Grail/Monty integration
- File and inline code providers
- CLI: `cairn up`, `cairn spawn`, `cairn accept`, `cairn reject`, `cairn status`

**Example usage:**
```python
from cairn import CairnOrchestrator
from cairn.providers import FileCodeProvider

# Use with file-based code
orch = CairnOrchestrator(
    code_provider=FileCodeProvider(base_path="./scripts")
)

await orch.initialize()
agent_id = await orch.spawn_agent("refactor_imports.pym")
```

### 8.2 Plugin: `cairn-llm`

**Installation:**
```bash
pip install cairn-llm
```

**Dependencies:**
```toml
[project]
dependencies = [
    "cairn>=0.2.0",
    "llm>=0.13.0",
    "anthropic>=0.40.0",  # If using Claude
]
```

**Usage:**
```python
from cairn import CairnOrchestrator
from cairn_llm import LLMCodeProvider

# Use with LLM code generation
orch = CairnOrchestrator(
    code_provider=LLMCodeProvider(
        model="claude-sonnet-4-5",
        temperature=0.2
    )
)

await orch.initialize()
# Now task is natural language
agent_id = await orch.spawn_agent("Add type hints to all public functions")
```

**CLI integration:**
```bash
cairn spawn "Add docstrings" --provider llm
cairn spawn "Add docstrings" --provider llm --model claude-opus-4-6
```

### 8.3 Plugin: `cairn-git`

**Installation:**
```bash
pip install cairn-git
```

**Usage:**
```python
from cairn import CairnOrchestrator
from cairn_git import GitCodeProvider

orch = CairnOrchestrator(
    code_provider=GitCodeProvider(
        cache_dir=Path.home() / ".cairn" / "git-cache"
    )
)

await orch.initialize()
agent_id = await orch.spawn_agent(
    "git://github.com/org/scripts@v1.0.0:refactor.pym"
)
```

### 8.4 Plugin: `cairn-registry`

**Installation:**
```bash
pip install cairn-registry
```

**Usage:**
```python
from cairn import CairnOrchestrator
from cairn_registry import RegistryCodeProvider

orch = CairnOrchestrator(
    code_provider=RegistryCodeProvider(
        registry_url="https://scripts.example.com",
        api_key=os.environ["REGISTRY_KEY"]
    )
)

await orch.initialize()
agent_id = await orch.spawn_agent("registry://company/format-code:latest")
```

### 8.5 Custom Providers

Users can create their own providers:

```python
# my_provider.py
from cairn.providers import CodeProvider

class DatabaseCodeProvider:
    """Load code from database."""

    def __init__(self, db_url: str):
        self.db = connect(db_url)

    async def get_code(self, reference: str, context: dict) -> str:
        # reference is a database ID
        result = await self.db.query(
            "SELECT code FROM scripts WHERE id = ?",
            reference
        )
        return result["code"]

# Use it
orch = CairnOrchestrator(
    code_provider=DatabaseCodeProvider("postgresql://...")
)
```

---

## 9. Migration Path

### 9.1 Phase 1: Update Dependencies

```toml
# pyproject.toml
[project]
dependencies = [
    "fsdantic>=0.3.0",  # New version
    "grail>=2.0.0",     # New version
    "pydantic>=2.0.0",
]

# LLM dependencies removed from core
# (will be in cairn-llm plugin)
```

### 9.2 Phase 2: Add Provider Abstraction

1. Create `src/cairn/providers.py`
2. Define `CodeProvider` protocol
3. Implement `FileCodeProvider`
4. Implement `InlineCodeProvider`
5. Update `CairnOrchestrator.__init__()` to accept `code_provider`

### 9.3 Phase 3: Extract LLM to Plugin

1. Create separate `cairn-llm` package
2. Move `code_generator.py` to `cairn_llm/provider.py`
3. Rename `CodeGenerator` to `LLMCodeProvider`
4. Implement `CodeProvider` protocol
5. Update prompts for .pym generation
6. Add CLI integration (--provider flag)

### 9.4 Phase 4: Update FSdantic Integration

1. Replace `Fsdantic.open_with_options()` with `Fsdantic.open()`
2. Replace `FileOperations` usage with `workspace.files`
3. Update KV operations to use `workspace.kv`
4. Update overlay operations to use `workspace.overlay`
5. Add materialization using `workspace.materialize`

### 9.5 Phase 5: Update Grail Integration

1. Remove `MontyContext` imports
2. Add `grail.load()` for .pym files
3. Update external function registration
4. Add `grail check` validation
5. Update error handling for new exception types

### 9.6 Phase 6: Update Orchestrator

1. Refactor agent execution flow for providers
2. Simplify external function creation
3. Update accept/reject logic with overlay manager
4. Improve error handling
5. Add provider-specific error handling

### 9.7 Phase 7: Update Tests

1. Update test fixtures for new APIs
2. Add provider tests (file, inline)
3. Add tests for grail check integration
4. Add tests for new workspace manager usage
5. Update integration tests
6. Add plugin tests (in separate packages)

### 9.8 Phase 8: Documentation

1. Update README to reflect general-purpose nature
2. Document provider interface
3. Create plugin development guide
4. Add examples for each built-in provider
5. Migration guide for V1 users
6. Update SPEC.md

---

## 10. Testing Strategy

### 10.1 Core Library Tests

```python
# tests/test_providers.py
async def test_file_code_provider():
    provider = FileCodeProvider(base_path="./test_scripts")
    code = await provider.get_code("example.pym", {})
    assert "from grail import" in code

async def test_inline_code_provider():
    provider = InlineCodeProvider()
    code = "print('hello')"
    result = await provider.get_code(code, {})
    assert result == code

# tests/test_orchestrator_with_providers.py
async def test_orchestrator_with_file_provider():
    orch = CairnOrchestrator(
        code_provider=FileCodeProvider(base_path="./test_scripts")
    )
    await orch.initialize()

    agent_id = await orch.spawn_agent("example.pym")
    # ... verify execution ...

# tests/test_workspace_managers.py
async def test_files_manager():
    ws = await Fsdantic.open(path=":memory:")
    await ws.files.write("/test.txt", "content")
    content = await ws.files.read("/test.txt")
    assert content == "content"

async def test_overlay_manager():
    stable = await Fsdantic.open(path=":memory:")
    agent = await Fsdantic.open(path=":memory:", overlay=stable)

    await agent.files.write("/test.txt", "agent content")

    # Merge
    result = await stable.overlay.merge(agent, strategy=MergeStrategy.OVERWRITE)
    assert result.files_merged == 1

    # Verify merge
    content = await stable.files.read("/test.txt")
    assert content == "agent content"
```

### 10.2 Plugin Tests

```python
# cairn-llm/tests/test_llm_provider.py
async def test_llm_code_provider():
    provider = LLMCodeProvider(model="gpt-4")

    code = await provider.get_code(
        "Add docstrings to functions",
        {"agent_id": "test"}
    )

    # Verify structure
    assert "from grail import" in code
    assert "@external" in code
    assert "submit_result" in code

async def test_llm_provider_validation():
    provider = LLMCodeProvider()

    # Valid code
    valid_code = """
from grail import external
@external
async def submit_result(summary: str, changed_files: list[str]) -> bool: ...
await submit_result("done", [])
"""
    is_valid, error = await provider.validate_code(valid_code)
    assert is_valid

    # Invalid code (missing submit_result)
    invalid_code = "print('hello')"
    is_valid, error = await provider.validate_code(invalid_code)
    assert not is_valid
```

### 10.3 Integration Tests

```python
# tests/integration/test_full_lifecycle.py
async def test_full_lifecycle_with_file_provider():
    """Test complete agent lifecycle with file-based code."""
    orch = CairnOrchestrator(
        code_provider=FileCodeProvider(base_path="./test_scripts")
    )
    await orch.initialize()

    # Spawn agent
    agent_id = await orch.spawn_agent("add_docstrings.pym")

    # Wait for REVIEWING state
    await wait_for_state(orch, agent_id, AgentState.REVIEWING)

    # Verify .pym file exists
    pym_file = Path(f".grail/agents/{agent_id}/task.pym")
    assert pym_file.exists()

    # Verify grail check passed
    check_file = Path(f".grail/agents/{agent_id}/check.json")
    assert check_file.exists()

    # Verify preview exists
    preview_dir = Path(f"~/.cairn/previews/{agent_id}").expanduser()
    assert preview_dir.exists()

    # Accept
    await orch.accept_agent(agent_id)

    # Verify state
    ctx = orch.active_agents.get(agent_id)
    assert ctx.state == AgentState.ACCEPTED
```

---

## 11. Implementation Phases

### Phase 1: Provider Foundation (Week 1)
- [ ] Create `src/cairn/providers.py` with protocol
- [ ] Implement `FileCodeProvider`
- [ ] Implement `InlineCodeProvider`
- [ ] Add provider parameter to orchestrator
- [ ] Basic provider tests
- [ ] Update documentation

### Phase 2: Extract LLM to Plugin (Week 2)
- [ ] Create `cairn-llm` package structure
- [ ] Move and adapt `code_generator.py` to `LLMCodeProvider`
- [ ] Update prompts for .pym generation
- [ ] Add plugin tests
- [ ] CLI integration (--provider flag)
- [ ] Plugin documentation

### Phase 3: FSdantic Integration (Week 3)
- [ ] Update workspace opening API
- [ ] Replace FileOperations with workspace.files
- [ ] Update KV operations with workspace.kv
- [ ] Update overlay operations with workspace.overlay
- [ ] Add materialization with workspace.materialize
- [ ] Update tests for new APIs

### Phase 4: Grail Integration (Week 4)
- [ ] Remove MontyContext
- [ ] Add grail.load() integration
- [ ] Add grail check validation
- [ ] Update external function registration
- [ ] Update error handling
- [ ] Integration tests

### Phase 5: Orchestrator Refactor (Week 5)
- [ ] Refactor agent execution for providers
- [ ] Simplify external functions
- [ ] Update accept/reject with overlay manager
- [ ] Improve error handling
- [ ] Performance optimization

### Phase 6: Additional Plugins (Week 6-7)
- [ ] Create `cairn-git` plugin
- [ ] Create `cairn-registry` plugin
- [ ] Plugin tests
- [ ] Plugin documentation
- [ ] Plugin examples

### Phase 7: Testing & Polish (Week 8)
- [ ] Complete test coverage (>90%)
- [ ] Integration test suite
- [ ] Performance benchmarks
- [ ] Code review and cleanup

### Phase 8: Documentation (Week 9)
- [ ] Update README for general-purpose nature
- [ ] Provider development guide
- [ ] Plugin usage examples
- [ ] Migration guide from V1
- [ ] Update SPEC.md
- [ ] Architecture diagrams

### Phase 9: Release (Week 10)
- [ ] Final review
- [ ] Version bump to v0.2.0
- [ ] Publish cairn core
- [ ] Publish plugins
- [ ] Announcement and blog post

---

## 12. Benefits Summary

### 12.1 General-Purpose Benefits

**Before (AI-Only):**
- Limited to LLM code generation use cases
- Requires LLM dependencies even if not needed
- Tight coupling to AI domain

**After (General-Purpose):**
- ✅ Run untrusted user scripts safely
- ✅ Preview environments for any code
- ✅ CI/CD with sandboxed execution
- ✅ File-based task orchestration
- ✅ Git-based code execution
- ✅ Registry-based script management
- ✅ Custom code sources via providers

### 12.2 Architecture Benefits

**Before:**
- Tight coupling between orchestrator and LLM
- Cannot test without mocking LLM
- Complex orchestrator with mixed concerns

**After:**
- ✅ Clear separation of concerns (orchestration vs code sourcing)
- ✅ Testable with deterministic file provider
- ✅ Pluggable code sources
- ✅ Simpler core with fewer dependencies

### 12.3 Developer Experience

**Before:**
- Agent code is invisible (strings)
- No IDE support
- Errors found at runtime
- Complex setup

**After:**
- ✅ Agent code in .pym files (visible)
- ✅ Full IDE support
- ✅ Pre-flight validation
- ✅ Simple setup
- ✅ Easy debugging

### 12.4 Plugin Ecosystem Benefits

**New capabilities:**
- ✅ Community can build providers
- ✅ Experiment with different code sources
- ✅ No need to fork cairn for custom sources
- ✅ Plugins can be versioned independently
- ✅ Optional features don't bloat core

### 12.5 Maintainability

**Before:**
- ~500 lines orchestrator
- Mixed concerns
- Hard to extend
- Complex abstractions

**After:**
- ✅ ~300 lines orchestrator
- ✅ Clear boundaries
- ✅ Easy to extend via providers
- ✅ Simple, focused abstractions

### 12.6 Cognitive Overhead Reduction

**Concepts removed:**
- ❌ `MontyContext` 13-parameter setup
- ❌ String-based code generation
- ❌ Manual tool registration
- ❌ Complex FileOperations setup

**Concepts added:**
- ✅ `CodeProvider` protocol (simple)
- ✅ Workspace managers (intuitive)
- ✅ `.pym` files (inspectable)
- ✅ `grail.load()` (3 parameters)

**Net result:** Fewer concepts, each simpler

---

## 13. Conclusion

This V2 refactor transforms Cairn from an AI-specific library into a **general-purpose sandboxed code orchestration runtime**. By:

1. **Extracting LLM generation to a plugin** - Core becomes AI-agnostic
2. **Adding provider abstraction** - Pluggable code sourcing
3. **Leveraging new FSdantic/Grail APIs** - Simpler, more powerful
4. **Focusing core on orchestration** - Clear boundaries

Cairn becomes:
- **More general-purpose** - Useful beyond AI agents
- **Simpler** - Fewer dependencies, clearer concerns
- **More extensible** - Plugin ecosystem
- **More testable** - Deterministic providers
- **More maintainable** - Focused core

**The essence of Cairn:**
> A workspace-aware orchestration runtime for sandboxed code execution with copy-on-write isolation and explicit human integration control.

This positions Cairn as foundational infrastructure for any use case requiring:
- Safe execution of untrusted code
- Workspace isolation
- Human review gates
- Preview environments
- State tracking and recovery

**Recommendation:** Proceed with V2 refactor. The architectural benefits and broader applicability justify the migration effort, especially with Cairn being a new library.
