# Cairn Refactoring Plan: Phase 1

## Executive Summary

This document outlines the Step 1 of refactoring of Cairn from its current implementation to a simpler, more powerful architecture built on top of two foundational libraries:

- **fsdantic**: Workspace-first, async Python library providing type-safe, Pydantic-based interface for AgentFS
- **grail**: Pydantic-native wrapper around Monty for executing untrusted Python code in sandboxed environments

The refactoring will significantly simplify Cairn's codebase by leveraging built-in functionality from these libraries, while maintaining all core features and improving type safety, observability, and extensibility.


## Detailed Refactoring Plan

### Phase 1: Replace Execution Layer with Grail

**Goal:** Replace `pydantic-monty` and `executor.py` with grail's `MontyContext`

#### 1.1 Create Agent Tools Registry (`agent_tools.py`)

**Current:** `external_functions.py` defines functions injected into Monty namespace

**New:** Define Pydantic-based tools for grail's tool registry

```python
# agent_tools.py
from pydantic import BaseModel, Field
from grail import Tool, ToolRegistry
from fsdantic import Workspace

# Input/Output models
class ReadFileInput(BaseModel):
    path: str = Field(description="Path to file to read")

class ReadFileOutput(BaseModel):
    content: str
    success: bool = True

class WriteFileInput(BaseModel):
    path: str
    content: str

class WriteFileOutput(BaseModel):
    success: bool = True

# ... similar for other operations

# Tool implementations using workspace
def create_agent_tools(workspace: Workspace) -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register_tool(
        name="read_file",
        description="Read a file from the workspace",
        input_model=ReadFileInput,
        output_model=ReadFileOutput
    )
    async def read_file(input: ReadFileInput) -> ReadFileOutput:
        content = await workspace.files.read(input.path)
        return ReadFileOutput(content=content)

    @registry.register_tool(
        name="write_file",
        description="Write a file to the workspace",
        input_model=WriteFileInput,
        output_model=WriteFileOutput
    )
    async def write_file(input: WriteFileInput) -> WriteFileOutput:
        await workspace.files.write(input.path, input.content)
        return WriteFileOutput()

    # ... register other tools: list_dir, file_exists, search_files,
    # search_content, ask_llm, submit_result, log

    return registry
```

**Benefits:**
- Type-safe inputs/outputs with Pydantic validation
- Automatic error handling by grail
- Observable tool calls (metrics, logging)
- Easy to extend with new tools

#### 1.2 Replace Executor with MontyContext

**Current:** `executor.py` wraps pydantic_monty with custom limits

**New:** Use grail's MontyContext with resource policies

```python
# In orchestrator.py or new execution.py module
from grail import MontyContext, ResourcePolicy, FilesystemPermissions
from grail.models import ExecutionInput, ExecutionOutput

async def execute_agent_code(
    agent_id: str,
    code: str,
    workspace: Workspace,
    settings: Settings
) -> ExecutionOutput:
    # Create tool registry for this agent
    tools = create_agent_tools(workspace)

    # Configure resource policy
    policy = ResourcePolicy(
        max_execution_time=settings.AGENT_TIMEOUT_SECONDS,
        max_memory_bytes=settings.AGENT_MAX_MEMORY,
        max_recursion_depth=1000
    )

    # Configure filesystem permissions (read-only, sandbox only)
    fs_perms = FilesystemPermissions(
        allow_read=False,  # No direct file access
        allow_write=False,
        allow_execute=False
    )

    # Create context
    ctx = MontyContext(
        tool_registry=tools,
        resource_policy=policy,
        filesystem_permissions=fs_perms,
        enable_metrics=True,
        enable_logging=True
    )

    # Execute
    input_data = ExecutionInput(code=code)
    result = await ctx.execute(input_data)

    return result
```

**Benefits:**
- Built-in resource enforcement (no manual limits)
- Automatic metrics collection
- Type-safe execution results
- Resumable execution support for future use
- Better error reporting

#### 1.3 Update Code Generator Prompts

**Current:** Prompts mention specific external functions by name

**New:** Update to reference grail tools

```python
# code_generator.py - update prompt template
AGENT_PROMPT_TEMPLATE = """
You are a Python code agent. Write code to accomplish this task:

{task}

Available tools (call via tool registry):
- read_file(path: str) -> str
- write_file(path: str, content: str) -> None
- list_dir(path: str) -> list[str]
- file_exists(path: str) -> bool
- search_files(pattern: str) -> list[str]
- search_content(pattern: str, path: str = ".") -> list[dict]
- ask_llm(question: str) -> str
- log(message: str) -> None
- submit_result(summary: str, changed_files: list[str]) -> None

Requirements:
- Use tools from the registry (already available in scope)
- No imports allowed
- Must call submit_result() when done
- Keep code simple and focused

Return only Python code.
"""
```

**Changes Required:**
- `code_generator.py`: Update prompt template
- Add validation for grail tool calls instead of function calls

---

## Implementation Strategy

### Phase 1: Foundation (Week 1-2)
**Goal:** Replace execution layer with grail

Tasks:
1. Create `agent_tools.py` with Pydantic-based tool definitions
2. Update `code_generator.py` prompt templates
3. Replace `executor.py` with grail MontyContext usage
4. Update tests for new execution model
5. Remove `executor.py`

**Success Criteria:**
- All existing tests pass with grail execution
- Agent code runs with new tool registry
- No regressions in functionality

---

## Migration Considerations

### Breaking Changes
**None for users** - The refactor is internal, CLI and behavior remain the same

**For contributors:**
- `external_functions.py` API removed → Use `agent_tools.py` tool registry
- `executor.py` removed → Use grail MontyContext
- `kv_models.py` removed → Use fsdantic TypedKVRepository
- `workspace.py` removed → Use workspace.materialize
