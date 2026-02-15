# Grail Library - Comprehensive Developer Guide

## Table of Contents
1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
3. [Installation](#installation)
4. [Quick Start](#quick-start)
5. [Core API Reference](#core-api-reference)
6. [Advanced Features](#advanced-features)
7. [Security Model](#security-model)
8. [Best Practices](#best-practices)
9. [Error Handling](#error-handling)
10. [Examples](#examples)

---

## Overview

**Grail** is a high-level Python wrapper designed to make **Monty**—a minimal, secure Python interpreter written in Rust—feel like a native part of the Pydantic ecosystem. It provides a Pythonic interface for executing untrusted code (such as AI-generated code) in a sandboxed environment with strong type safety and resource controls.

### Key Features

- **Schema as Contract**: Use Pydantic models to define the data "shape" of the sandbox
- **Safety by Default**: Leverage Monty's filesystem and network isolation with explicit permission controls
- **Developer Ergonomics**: Transform standard Python functions into secure, isolated execution with a single decorator
- **AOT Type Checking**: Automatic generation of type stubs for Monty's internal type-checker
- **Resumable Execution**: Pause and resume execution with snapshot support
- **Resource Control**: Fine-grained limits on memory, execution time, and recursion depth
- **Observability**: Built-in metrics collection and structured logging

### Architecture

```
Your Application
       ↓
   Grail Layer (Python)
       ↓
  Pydantic Validation + Type Stub Generation
       ↓
   Monty Runtime (Rust)
       ↓
  Isolated Execution (<1μs startup)
```

---

## Core Concepts

### 1. MontyContext

The **orchestrator** that manages the lifecycle of a Monty instance. It consumes Pydantic models and functions, generates environment stubs, and handles the `execute` / `start` / `resume` cycle.

### 2. Type Stubs

Grail automatically generates Python type stubs from your Pydantic models and tool signatures. These stubs enable Monty's ahead-of-time type checker to validate code before execution.

### 3. Resource Policies

Named presets (STRICT_POLICY, PERMISSIVE_POLICY, AI_AGENT_POLICY) that define execution limits. Policies can inherit from each other and compose using strictest-wins semantics.

### 4. GrailFilesystem

An explicit permission model for filesystem access within the sandbox. By default, all filesystem access is denied unless explicitly granted.

### 5. Tools

Functions that can be called from within the sandbox. Tools are registered with the context and automatically type-checked.

---

## Installation

```bash
pip install grail
```

**Requirements:**
- Python >= 3.13
- pydantic >= 2.12.5
- pydantic_monty (automatically installed)

---

## Quick Start

### Basic Example

```python
from pydantic import BaseModel
from grail import MontyContext

# Define input schema
class InputModel(BaseModel):
    a: int
    b: int

# Define output schema
class OutputModel(BaseModel):
    total: int

# Create context
ctx = MontyContext(InputModel, output_model=OutputModel)

# Execute code
result = ctx.execute(
    "{'total': inputs['a'] + inputs['b']}",
    {"a": 20, "b": 22}
)

print(result.total)  # 42
```

### Using the @secure Decorator

```python
from pydantic import BaseModel
from grail import secure

class Result(BaseModel):
    value: int

@secure()
def add(a: int, b: int) -> Result:
    return Result(value=a + b)

result = add(10, 32)  # Executed in isolated sandbox
print(result.value)  # 42
```

---

## Core API Reference

### MontyContext

The primary class for managing isolated execution environments.

#### Constructor

```python
MontyContext(
    input_model: type[BaseModel],
    limits: ResourceLimits | None = None,
    guard: ResourceGuard | dict[str, Any] | None = None,
    policy: PolicySpec | list[PolicySpec] | tuple[PolicySpec, ...] | None = None,
    output_model: type[BaseModel] | None = None,
    tools: list[Callable[..., Any]] | None = None,
    filesystem: Any | None = None,
    debug: bool = False,
    logger: StructuredLogger | None = None,
    metrics: MetricsCollector | None = None,
)
```

**Parameters:**

- **input_model** (type[BaseModel], required): Pydantic model defining the structure of inputs passed to the sandbox
- **output_model** (type[BaseModel], optional): Pydantic model for validating execution results. If None, raw output is returned
- **limits** (ResourceLimits, optional): Direct resource limits (highest precedence)
- **guard** (ResourceGuard | dict, optional): Validated resource limits object or dict
- **policy** (str | ResourcePolicy | list, optional): Named policy or custom policy object(s). Supports: "strict", "permissive", "ai_agent"
- **tools** (list[Callable], optional): Functions available within the sandbox
- **filesystem** (GrailFilesystem, optional): Filesystem adapter for controlled file access
- **debug** (bool, default=False): Enable debug mode to capture tool calls, stdout, stderr
- **logger** (StructuredLogger, optional): Custom structured logger instance
- **metrics** (MetricsCollector, optional): Custom metrics collector instance

**Limit Resolution Precedence (lowest to highest):**
1. Default limits
2. Policy-based limits
3. Guard-based limits
4. Explicit limits parameter

#### Methods

##### execute()

Synchronous execution of code in the sandbox.

```python
def execute(
    self,
    code: str,
    inputs: InputT | dict[str, Any]
) -> Any | OutputT
```

**Parameters:**
- **code** (str): Python code to execute. The code has access to:
  - `inputs`: Dictionary containing validated input data
  - Registered tools as callable functions
  - Standard Python builtins (subject to Monty restrictions)
- **inputs** (InputT | dict): Input data, validated against input_model

**Returns:** Validated output (if output_model specified) or raw result

**Raises:**
- `GrailValidationError`: Input validation failed
- `GrailExecutionError`: Code execution failed
- `GrailLimitError`: Resource limit exceeded
- `GrailOutputValidationError`: Output validation failed

**Important:** Cannot be called from within an active event loop. Use `execute_async()` instead.

##### execute_async()

Asynchronous execution of code in the sandbox.

```python
async def execute_async(
    self,
    code: str,
    inputs: InputT | dict[str, Any]
) -> Any | OutputT
```

Same parameters and return type as `execute()`, but runs asynchronously.

##### execute_with_resilience()

Execute with automatic retry and fallback behavior for production scenarios.

```python
def execute_with_resilience(
    self,
    code: str,
    inputs: InputT | dict[str, Any],
    *,
    retry_policy: RetryPolicy | None = None,
    fallback: Any | None = None,
) -> Any | OutputT
```

**Parameters:**
- **code**, **inputs**: Same as execute()
- **retry_policy** (RetryPolicy, optional): Retry configuration
- **fallback** (Any, optional): Value to return if all retries fail

**Behavior:**
- Retries execution based on policy
- Returns fallback value if all attempts fail (instead of raising)
- Emits lifecycle events via logger
- Increments metrics counters

##### start()

Begin execution in resumable mode, returning a snapshot at the first tool call.

```python
async def start(
    self,
    code: str,
    inputs: InputT | dict[str, Any]
) -> MontySnapshot
```

**Returns:** MontySnapshot - paused execution state

**Use Case:** When you need human-in-the-loop approval for tool calls or multi-step agent execution.

##### load_snapshot()

Restore a paused snapshot from serialized bytes.

```python
def load_snapshot(
    self,
    serialized: bytes,
    **kwargs: Any
) -> MontySnapshot
```

**Parameters:**
- **serialized** (bytes): Snapshot payload from `snapshot.dump()`
- **kwargs**: Additional Monty-specific load parameters

**Returns:** MontySnapshot ready to resume

#### Properties

##### debug_payload

Access debug information from the last execution.

```python
@property
def debug_payload(self) -> DebugPayload
```

**Returns:** Dictionary containing:
- **events** (list[str]): Lifecycle events
- **stdout** (str): Captured standard output
- **stderr** (str): Captured standard error
- **tool_calls** (list[DebugToolCall]): Detailed tool invocation log
- **resource_metrics** (dict): Resource usage statistics

**Note:** Only populated when `debug=True`

---

### @secure Decorator

Syntactic sugar for wrapping standard Python functions with automatic sandboxing.

```python
@secure(
    limits: ResourceLimits | None = None,
    tools: list[Callable[..., Any]] | None = None,
    debug: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]
```

**Parameters:**
- **limits** (ResourceLimits, optional): Resource constraints
- **tools** (list[Callable], optional): External functions available in sandbox
- **debug** (bool, default=False): Enable debug mode

**Behavior:**
1. Extracts function source code
2. Infers input model from function signature
3. Infers output model from return type annotation
4. Creates a MontyContext internally
5. Executes function body in sandbox
6. Returns validated result

**Example:**

```python
from pydantic import BaseModel
from grail import secure

class Point(BaseModel):
    x: float
    y: float

@secure()
def distance(p1: Point, p2: Point) -> float:
    import math
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    return math.sqrt(dx*dx + dy*dy)

result = distance(
    Point(x=0, y=0),
    Point(x=3, y=4)
)  # Returns 5.0, executed in sandbox
```

**Limitations:**
- Cannot use closures or external variables
- Source code must be available (no compiled-only modules)
- Decorators are stripped from the executed code

---

### ResourceLimits

TypedDict defining resource constraints for Monty execution.

```python
class ResourceLimits(TypedDict, total=False):
    max_allocations: int      # Maximum object allocations
    max_duration_secs: float   # Execution timeout in seconds
    max_memory: int            # Memory limit in bytes
    gc_interval: int           # Garbage collection interval
    max_recursion_depth: int   # Maximum call stack depth
```

**Default Values:**
```python
{
    "max_duration_secs": 1.0,
    "max_memory": 16 * 1024 * 1024,  # 16 MB
    "max_recursion_depth": 200,
}
```

**Usage:**

```python
limits: ResourceLimits = {
    "max_duration_secs": 2.0,
    "max_memory": 32 * 1024 * 1024,
}
ctx = MontyContext(InputModel, limits=limits)
```

#### Helper Functions

##### merge_resource_limits()

Merge custom limits with defaults.

```python
def merge_resource_limits(overrides: ResourceLimits | None) -> ResourceLimits
```

---

### ResourceGuard

Validated resource limits model with Pydantic validation.

```python
class ResourceGuard(BaseModel):
    max_allocations: int | None = Field(default=None, ge=1)
    max_duration_secs: float | None = Field(default=None, gt=0)
    max_memory: int | None = Field(default=None, ge=1024)
    gc_interval: int | None = Field(default=None, ge=1)
    max_recursion_depth: int | None = Field(default=None, ge=1)
```

**Validation:**
- max_allocations: Must be >= 1
- max_duration_secs: Must be > 0
- max_memory: Must be >= 1024 bytes
- gc_interval: Must be >= 1
- max_recursion_depth: Must be >= 1

**Methods:**

```python
def to_monty_limits(self) -> ResourceLimits
```
Returns dict with None values excluded, suitable for Monty execution.

**Usage:**

```python
guard = ResourceGuard(
    max_duration_secs=0.5,
    max_memory=8 * 1024 * 1024
)
ctx = MontyContext(InputModel, guard=guard)
```

---

### ResourcePolicy

Named resource policy with inheritance support.

```python
class ResourcePolicy(BaseModel):
    name: str
    guard: ResourceGuard = Field(default_factory=ResourceGuard)
    inherits: tuple[str, ...] = ()
```

**Fields:**
- **name** (str): Unique policy identifier
- **guard** (ResourceGuard): Resource limits for this policy
- **inherits** (tuple[str]): Names of parent policies to inherit from

#### Built-in Policies

##### STRICT_POLICY

```python
STRICT_POLICY = ResourcePolicy(
    name="strict",
    guard=ResourceGuard(
        max_duration_secs=0.5,
        max_memory=8 * 1024 * 1024,
        max_recursion_depth=120,
    ),
)
```

**Use Case:** High-security environments, short-lived operations

##### PERMISSIVE_POLICY

```python
PERMISSIVE_POLICY = ResourcePolicy(
    name="permissive",
    guard=ResourceGuard(
        max_duration_secs=3.0,
        max_memory=64 * 1024 * 1024,
        max_recursion_depth=400,
    ),
)
```

**Use Case:** Complex computations, trusted code

##### AI_AGENT_POLICY

```python
AI_AGENT_POLICY = ResourcePolicy(
    name="ai_agent",
    inherits=("strict",),  # Inherits from strict, then overrides
    guard=ResourceGuard(
        max_duration_secs=1.5,
        max_memory=32 * 1024 * 1024,
        max_recursion_depth=250,
    ),
)
```

**Use Case:** AI-generated code with moderate complexity

#### Policy Functions

##### resolve_policy()

Resolve named/custom policies into a concrete ResourceGuard.

```python
def resolve_policy(
    policy: PolicySpec | list[PolicySpec] | tuple[PolicySpec, ...] | None,
    *,
    available_policies: dict[str, ResourcePolicy] | None = None,
) -> ResourceGuard | None
```

**Parameters:**
- **policy**: Policy name(s) or ResourcePolicy object(s)
- **available_policies**: Custom policy registry (defaults to NAMED_POLICIES)

**Behavior:**
- Multiple policies combine using strictest-wins semantics
- Supports policy inheritance with cycle detection
- Returns None if policy is None

**Example:**

```python
# Single policy
guard = resolve_policy("strict")

# Multiple policies (strictest wins)
guard = resolve_policy(["permissive", "strict"])  # Effectively strict

# Custom policy
custom = ResourcePolicy(
    name="custom",
    guard=ResourceGuard(max_duration_secs=2.0)
)
guard = resolve_policy(custom)
```

##### resolve_effective_limits()

Resolve final runtime limits from all sources.

```python
def resolve_effective_limits(
    *,
    limits: ResourceLimits | None,
    guard: ResourceGuard | dict[str, Any] | None,
    policy: PolicySpec | list[PolicySpec] | tuple[PolicySpec, ...] | None,
) -> ResourceLimits
```

**Precedence (lowest to highest):**
1. Default limits
2. Policy limits
3. Guard limits
4. Explicit limits

**Usage:**

```python
effective = resolve_effective_limits(
    policy="strict",
    guard=ResourceGuard(max_memory=16*1024*1024),
    limits={"max_duration_secs": 2.0}
)
# Result: strict policy base + guard memory + explicit duration
```

---

### GrailFilesystem

Guarded OS adapter with explicit path permissions.

```python
class GrailFilesystem(AbstractOS):
    def __init__(
        self,
        os_access: AbstractOS,
        *,
        root_dir: str | PurePosixPath = "/",
        permissions: Mapping[str | PurePosixPath, FilePermission] | None = None,
        default_permission: FilePermission = FilePermission.DENY,
        read_hooks: Mapping[str | PurePosixPath, ReadHook] | None = None,
        write_hooks: Mapping[str | PurePosixPath, WriteHook] | None = None,
    )
```

**Parameters:**
- **os_access** (AbstractOS): Underlying filesystem adapter (e.g., OSAccess with MemoryFile)
- **root_dir** (str | PurePosixPath): Sandbox root directory (default: "/")
- **permissions** (dict): Path-to-permission mapping
- **default_permission** (FilePermission): Default permission for unlisted paths (default: DENY)
- **read_hooks** (dict): Callbacks for reading specific paths
- **write_hooks** (dict): Callbacks for writing specific paths

#### FilePermission Enum

```python
class FilePermission(str, Enum):
    READ = "read"    # Read-only access
    WRITE = "write"  # Read and write access
    DENY = "deny"    # No access
```

#### Security Features

1. **Path Normalization**: All paths are normalized and checked against root_dir
2. **Traversal Prevention**: Rejects paths containing ".." or escaping root
3. **Hierarchical Permissions**: Permissions cascade from parent to child paths
4. **Hook System**: Intercept reads/writes for dynamic content or auditing

**Example:**

```python
from grail import GrailFilesystem, FilePermission, memory_filesystem

# Create in-memory filesystem
fs = memory_filesystem(
    files={
        "/data/input.txt": "Hello, World!",
        "/data/config.json": '{"key": "value"}',
    },
    permissions={
        "/data": FilePermission.READ,
        "/output": FilePermission.WRITE,
    },
    default_permission=FilePermission.DENY
)

ctx = MontyContext(InputModel, filesystem=fs)
result = ctx.execute("""
from pathlib import Path
content = Path('/data/input.txt').read_text()
Path('/output/result.txt').write_text(content.upper())
{'status': 'done'}
""", {})
```

#### Filesystem Factory Functions

##### memory_filesystem()

Create a pure in-memory filesystem.

```python
def memory_filesystem(
    files: Mapping[str | PurePosixPath, str | bytes],
    *,
    root_dir: str | PurePosixPath = "/",
    permissions: Mapping[str | PurePosixPath, FilePermission] | None = None,
    default_permission: FilePermission = FilePermission.DENY,
) -> GrailFilesystem
```

##### hooked_filesystem()

Create a filesystem with prefix-based hooks.

```python
def hooked_filesystem(
    *,
    read_hooks: Mapping[str | PurePosixPath, ReadHook] | None = None,
    write_hooks: Mapping[str | PurePosixPath, WriteHook] | None = None,
    files: Mapping[str | PurePosixPath, str | bytes] | None = None,
    root_dir: str | PurePosixPath = "/",
    permissions: Mapping[str | PurePosixPath, FilePermission] | None = None,
    default_permission: FilePermission = FilePermission.DENY,
) -> GrailFilesystem
```

**Hook Signatures:**
```python
ReadHook = Callable[[PurePosixPath], str | bytes]
WriteHook = Callable[[PurePosixPath, str | bytes], None]
```

**Use Case:** Dynamic content generation, logging, or database-backed files

**Example:**

```python
def read_config(path: PurePosixPath) -> str:
    # Dynamically generate config
    return '{"timestamp": "' + str(time.time()) + '"}'

def log_write(path: PurePosixPath, content: str | bytes) -> None:
    print(f"Write to {path}: {len(content)} bytes")

fs = hooked_filesystem(
    read_hooks={"/config": read_config},
    write_hooks={"/logs": log_write},
    permissions={"/config": FilePermission.READ, "/logs": FilePermission.WRITE}
)
```

##### callback_filesystem()

Create a filesystem with per-file callback pairs.

```python
def callback_filesystem(
    files: Mapping[str | PurePosixPath, tuple[ReadHook, WriteHook]],
    *,
    root_dir: str | PurePosixPath = "/",
    permissions: Mapping[str | PurePosixPath, FilePermission] | None = None,
    default_permission: FilePermission = FilePermission.DENY,
    read_hooks: Mapping[str | PurePosixPath, ReadHook] | None = None,
    write_hooks: Mapping[str | PurePosixPath, WriteHook] | None = None,
) -> GrailFilesystem
```

---

### ToolRegistry

Manages deterministic tool registration and invocation.

```python
class ToolRegistry:
    def __init__(self, tools: list[Callable[..., Any]] | None = None)
```

**Methods:**

##### register()

Register a tool with optional custom name.

```python
def register(
    self,
    tool: Callable[..., Any],
    *,
    name: str | None = None
) -> None
```

**Raises:** ValueError if tool name already registered

##### invoke()

Call a registered tool by name.

```python
async def invoke(self, name: str, *args: Any, **kwargs: Any) -> Any
```

**Raises:** KeyError if tool not found

**Behavior:** Automatically handles async tools

##### as_mapping()

Get deterministic ordered mapping of tools.

```python
def as_mapping(self) -> dict[str, Callable[..., Any]]
```

**Returns:** Dictionary sorted by tool name for deterministic stub generation

**Usage:**

```python
def add(a: int, b: int) -> int:
    return a + b

def multiply(a: int, b: int) -> int:
    return a * b

registry = ToolRegistry([add, multiply])
print(registry.names)  # ('add', 'multiply')
```

---

### MontySnapshot

Wrapper around paused/completed Monty execution state.

```python
class MontySnapshot:
    def __init__(
        self,
        state: Any,
        *,
        validate_output: Callable[[Any], Any],
        normalize_exception: Callable[[Exception], Exception],
    )
```

**Note:** Typically created by `MontyContext.start()` or `load_snapshot()`, not directly instantiated.

#### Properties

##### function_name

Name of the function that caused the pause (tool call).

```python
@property
def function_name(self) -> str | None
```

**Returns:** Tool name or None if execution is complete

##### args

Positional arguments for the paused tool call.

```python
@property
def args(self) -> tuple[Any, ...] | None
```

##### kwargs

Keyword arguments for the paused tool call.

```python
@property
def kwargs(self) -> dict[str, Any] | None
```

##### is_complete

Check if execution has finished.

```python
@property
def is_complete(self) -> bool
```

**Returns:** True if no more tool calls pending

##### final_value / output

Get the validated final output.

```python
@property
def final_value(self) -> Any

@property
def output(self) -> Any  # Backward-compatible alias
```

**Raises:** RuntimeError if snapshot is not complete

#### Methods

##### dump()

Serialize paused state to bytes for storage.

```python
def dump(self) -> bytes
```

**Raises:** RuntimeError if snapshot is complete

**Use Case:** Persist execution state to database, file, or network

##### resume()

Resume execution with tool result.

```python
def resume(self, *args: Any, **kwargs: Any) -> MontySnapshot
```

**Parameters:**
- **args, kwargs**: Return value to provide for the paused tool call. Typically a single positional arg with the tool result.

**Returns:** New MontySnapshot (either paused at next tool call or complete)

**Example:**

```python
# Start execution
snapshot = await ctx.start(code, inputs)

while not snapshot.is_complete:
    # Get tool call details
    tool_name = snapshot.function_name
    tool_args = snapshot.args
    tool_kwargs = snapshot.kwargs

    # Execute tool (with human approval, logging, etc.)
    result = await execute_tool(tool_name, *tool_args, **tool_kwargs)

    # Resume with result
    snapshot = snapshot.resume(result)

# Get final output
final = snapshot.final_value
```

#### Snapshot Serialization Helpers

##### snapshot_payload_to_base64()

Encode snapshot bytes to URL-safe base64.

```python
def snapshot_payload_to_base64(payload: bytes) -> str
```

##### snapshot_payload_from_base64()

Decode base64 back to snapshot bytes.

```python
def snapshot_payload_from_base64(payload: str) -> bytes
```

**Raises:** ValueError if payload is invalid

**Example:**

```python
# Persist to database
snapshot = await ctx.start(code, inputs)
serialized = snapshot.dump()
b64_string = snapshot_payload_to_base64(serialized)
db.save("execution_123", b64_string)

# Later: restore
b64_string = db.load("execution_123")
serialized = snapshot_payload_from_base64(b64_string)
snapshot = ctx.load_snapshot(serialized)
snapshot = snapshot.resume(tool_result)
```

##### serialize_snapshot_payload()

Identity function for explicit serialization.

```python
def serialize_snapshot_payload(payload: bytes) -> bytes
```

##### deserialize_snapshot_payload()

Normalize bytes-like to immutable bytes.

```python
def deserialize_snapshot_payload(payload: bytes | bytearray | memoryview) -> bytes
```

---

### StubGenerator

Generates deterministic type stubs for Monty's type checker.

```python
class StubGenerator:
    def __init__(self)

    def generate(
        self,
        *,
        input_model: type[BaseModel],
        output_model: type[BaseModel] | None,
        tools: list[Callable[..., Any]],
    ) -> str
```

**Returns:** Complete Python stub file as string

**Behavior:**
1. Analyzes all Pydantic models and tool signatures
2. Collects type annotations (including nested models, NewTypes, TypeAliases)
3. Generates TypedDict representations for BaseModels
4. Generates function signatures for tools
5. Returns deterministic, sorted output

**Generated Stub Example:**

```python
from typing import TypedDict

class InputModel(TypedDict):
    a: int
    b: int

class OutputModel(TypedDict):
    total: int

def add(x: int, y: int) -> int: ...
```

**Supported Types:**
- Primitive types (int, str, float, bool, None)
- Collections (list, dict, set, tuple)
- Unions and Optional
- Pydantic BaseModel (converted to TypedDict)
- dataclasses
- Callable types
- NewType
- TypeAlias
- Generic types

**Important:** Automatically invoked by MontyContext, rarely used directly.

---

### Observability

#### MetricsCollector

In-memory metrics collector with counters and timers.

```python
class MetricsCollector:
    def __init__(self)
```

**Methods:**

##### increment()

Increment a counter.

```python
def increment(self, name: str, value: int = 1) -> None
```

##### observe_ms()

Record a timing observation.

```python
def observe_ms(self, name: str, duration_ms: float) -> None
```

##### timer()

Context manager for automatic timing.

```python
@contextmanager
def timer(self, name: str)
```

**Example:**

```python
metrics = MetricsCollector()

metrics.increment("requests")

with metrics.timer("execution"):
    result = ctx.execute(code, inputs)

stats = metrics.snapshot()
print(stats)
# {
#   "counters": {"requests": 1},
#   "timings": {
#     "execution": {"count": 1, "min_ms": 45.2, "max_ms": 45.2, "avg_ms": 45.2}
#   }
# }
```

##### snapshot()

Get current metrics as dict.

```python
def snapshot(self) -> dict[str, Any]
```

**Returns:**
```python
{
    "counters": dict[str, int],
    "timings": dict[str, dict[str, float]]  # {name: {count, min_ms, max_ms, avg_ms}}
}
```

#### StructuredLogger

JSON-compatible logger hook.

```python
class StructuredLogger:
    def __init__(self, sink: Callable[[dict[str, Any]], None] | None = None)
```

**Parameters:**
- **sink**: Optional callback for log events (defaults to no-op)

**Methods:**

##### emit()

Emit a structured log event.

```python
def emit(self, event: str, **payload: Any) -> None
```

**Example:**

```python
def log_sink(event_data: dict):
    print(json.dumps(event_data))

logger = StructuredLogger(sink=log_sink)
ctx = MontyContext(InputModel, logger=logger)

# Logs emitted during execution:
# {"event": "grail.lifecycle", "step": "validate-inputs"}
# {"event": "grail.lifecycle", "step": "build-runner"}
# {"event": "grail.lifecycle", "step": "execute"}
# {"event": "grail.lifecycle", "step": "validate-output"}
# {"event": "grail.lifecycle", "step": "finished"}
```

**Built-in Events:**
- `grail.lifecycle` - Execution phase changes (step: validate-inputs, build-runner, execute, validate-output, finished, restore-snapshot)
- `grail.execution.attempt` - Retry attempt (when using execute_with_resilience)
- `grail.execution.failure` - Execution failure
- `grail.execution.fallback` - Fallback value used

#### RetryPolicy

Retry configuration for resilient execution.

```python
@dataclass
class RetryPolicy:
    attempts: int = 1
    backoff_seconds: float = 0.0
    retry_on: tuple[type[Exception], ...] = (Exception,)
```

**Fields:**
- **attempts** (int): Total attempts (including initial try)
- **backoff_seconds** (float): Delay between retry attempts
- **retry_on** (tuple): Exception types that trigger retry

**Methods:**

```python
def should_retry(self, error: Exception, *, attempt: int) -> bool
```

**Example:**

```python
policy = RetryPolicy(
    attempts=3,
    backoff_seconds=1.0,
    retry_on=(GrailLimitError,)
)

result = ctx.execute_with_resilience(
    code,
    inputs,
    retry_policy=policy,
    fallback={"status": "failed"}
)
```

#### ResourceUsageMetrics

Runtime metrics exposed after execution.

```python
class ResourceUsageMetrics(BaseModel):
    max_allocations: int | None = None
    max_duration_secs: float | None = None
    max_memory: int | None = None
    gc_interval: int | None = None
    max_recursion_depth: int | None = None
    exceeded: list[str] = Field(default_factory=list)
```

**Fields:**
- **exceeded** (list[str]): List of limit types that were exceeded (e.g., ["runtime_limit"])

**Access:**

```python
result = ctx.execute(code, inputs)
metrics = ctx.resource_metrics
print(f"Exceeded limits: {metrics.exceeded}")
```

---

## Error Handling

### Exception Hierarchy

```
Exception
├── ValueError
│   └── GrailValidationError
│       └── GrailOutputValidationError
└── RuntimeError
    └── GrailExecutionError
        └── GrailLimitError
```

### Error Types

#### GrailValidationError

Raised when input validation fails.

```python
class GrailValidationError(ValueError)
```

**When Raised:**
- Input data doesn't match input_model schema
- Type mismatches, missing required fields, validation constraints violated

**Example:**

```python
class InputModel(BaseModel):
    age: int = Field(ge=0)

ctx = MontyContext(InputModel)
try:
    ctx.execute("inputs['age']", {"age": -5})
except GrailValidationError as e:
    print(e)
    # "Input validation failed for InputModel: age: Input should be greater than or equal to 0"
```

#### GrailOutputValidationError

Raised when output validation fails.

```python
class GrailOutputValidationError(GrailValidationError)
```

**When Raised:**
- Code returns data that doesn't match output_model
- Type mismatches in output

**Example:**

```python
class OutputModel(BaseModel):
    count: int

ctx = MontyContext(InputModel, output_model=OutputModel)
try:
    ctx.execute("{'count': 'not a number'}", {})
except GrailOutputValidationError as e:
    print(e)
```

#### GrailExecutionError

Raised when code execution fails.

```python
class GrailExecutionError(RuntimeError)
```

**When Raised:**
- Syntax errors in code
- Runtime exceptions (AttributeError, KeyError, etc.)
- Monty-specific execution failures
- Filesystem permission denied

**Example:**

```python
try:
    ctx.execute("1 / 0", inputs)
except GrailExecutionError as e:
    print(e)  # "Monty execution failed: division by zero"
```

#### GrailLimitError

Raised when resource limits are exceeded.

```python
class GrailLimitError(GrailExecutionError)
```

**When Raised:**
- Execution time exceeds max_duration_secs
- Memory usage exceeds max_memory
- Recursion depth exceeds max_recursion_depth

**Example:**

```python
ctx = MontyContext(InputModel, limits={"max_recursion_depth": 30})
try:
    ctx.execute("""
def recurse(n):
    return recurse(n + 1)
recurse(0)
""", inputs)
except GrailLimitError as e:
    print(e)  # "Monty runtime error: maximum recursion depth exceeded"
    print(ctx.resource_metrics.exceeded)  # ["runtime_limit"]
```

#### PolicyValidationError

Raised when policy configuration is invalid.

```python
class PolicyValidationError(ValueError)
```

**When Raised:**
- Policy inheritance cycle detected
- Unknown policy name referenced
- Conflicting policy definitions

**Example:**

```python
# Circular inheritance
policy1 = ResourcePolicy(name="a", inherits=("b",))
policy2 = ResourcePolicy(name="b", inherits=("a",))
try:
    resolve_policy(policy1, available_policies={"a": policy1, "b": policy2})
except PolicyValidationError as e:
    print(e)  # "Policy inheritance cycle detected: a -> b -> a"
```

### Error Message Formatting

#### format_validation_error()

Format Pydantic validation errors.

```python
def format_validation_error(prefix: str, exc: ValidationError) -> str
```

**Returns:** Human-readable error with dotted field paths

#### format_runtime_error()

Format runtime errors with location info.

```python
def format_runtime_error(
    *,
    category: str,
    exc: Exception,
    location: ErrorLocation | None = None,
) -> str
```

#### ErrorLocation

```python
@dataclass
class ErrorLocation:
    line: int | None = None
    column: int | None = None
```

---

## Advanced Features

### Human-in-the-Loop Execution

Use snapshots for multi-step execution with approval gates.

```python
async def execute_with_approval(ctx, code, inputs):
    snapshot = await ctx.start(code, inputs)

    while not snapshot.is_complete:
        # Pause before each tool call
        print(f"About to call: {snapshot.function_name}")
        print(f"Arguments: {snapshot.args}, {snapshot.kwargs}")

        # Get human approval
        approved = input("Approve? (y/n): ") == "y"
        if not approved:
            raise RuntimeError("User rejected tool call")

        # Execute tool
        tool = ctx.tools.as_mapping()[snapshot.function_name]
        result = await ctx.tools.invoke(
            snapshot.function_name,
            *snapshot.args,
            **snapshot.kwargs
        )

        # Resume with result
        snapshot = snapshot.resume(result)

    return snapshot.final_value
```

### Persistent Execution

Store and resume execution across process boundaries.

```python
# Process 1: Start execution
snapshot = await ctx.start(code, inputs)
if not snapshot.is_complete:
    serialized = snapshot.dump()
    redis.set("execution:123", serialized)
    redis.set("execution:123:tool", snapshot.function_name)

# Process 2: Resume execution
serialized = redis.get("execution:123")
snapshot = ctx.load_snapshot(serialized)
tool_result = compute_tool_result()
snapshot = snapshot.resume(tool_result)
redis.set("execution:123", snapshot.dump() if not snapshot.is_complete else None)
```

### Custom Policy Composition

Create domain-specific policies.

```python
# Define custom policies
DATA_PROCESSING_POLICY = ResourcePolicy(
    name="data_processing",
    guard=ResourceGuard(
        max_duration_secs=5.0,
        max_memory=128 * 1024 * 1024,
    )
)

ANALYTICS_POLICY = ResourcePolicy(
    name="analytics",
    inherits=("data_processing",),
    guard=ResourceGuard(
        max_recursion_depth=500,
    )
)

# Use in context
ctx = MontyContext(
    InputModel,
    policy=[ANALYTICS_POLICY, "strict"],  # Combines both
)
```

### Dynamic Filesystem with Hooks

Implement database-backed or network-backed files.

```python
def read_from_db(path: PurePosixPath) -> str:
    record_id = path.name
    return database.fetch(record_id)

def write_to_db(path: PurePosixPath, content: str | bytes) -> None:
    record_id = path.name
    database.store(record_id, content)

fs = hooked_filesystem(
    read_hooks={"/db": read_from_db},
    write_hooks={"/db": write_to_db},
    permissions={"/db": FilePermission.WRITE}
)

ctx = MontyContext(InputModel, filesystem=fs)
result = ctx.execute("""
from pathlib import Path
data = Path('/db/user_123').read_text()
processed = data.upper()
Path('/db/user_123_processed').write_text(processed)
{'status': 'done'}
""", inputs)
```

### Metrics and Monitoring

Track execution patterns for observability.

```python
metrics = MetricsCollector()

def log_handler(event: dict):
    if event["event"] == "grail.lifecycle":
        print(f"Phase: {event['step']}")

logger = StructuredLogger(sink=log_handler)
ctx = MontyContext(InputModel, metrics=metrics, logger=logger)

for i in range(100):
    try:
        ctx.execute(code, inputs[i])
    except GrailLimitError:
        metrics.increment("limit_exceeded")

stats = metrics.snapshot()
print(f"Success rate: {stats['counters'].get('executions_success', 0)} / 100")
print(f"Avg execution time: {stats['timings']['execute_async']['avg_ms']} ms")
```

---

## Security Model

### Threat Model

Grail protects against:

1. **Filesystem Access**: Unauthorized reads/writes to host filesystem
2. **Network Access**: Unauthorized network egress (blocked by Monty)
3. **Resource Exhaustion**: Infinite loops, memory bombs
4. **Tool Abuse**: Untyped or unchecked tool arguments
5. **Data Leakage**: Sensitive data in errors/logs

### Security Controls

1. **Default Deny**: All filesystem access denied by default
2. **Explicit Permissions**: GrailFilesystem requires explicit path permissions
3. **Path Traversal Prevention**: Automatic normalization and validation
4. **Resource Limits**: Configurable limits on time, memory, recursion
5. **Type Safety**: Pydantic validation for all inputs/outputs/tools
6. **Sandboxing**: Monty runtime isolation
7. **Audit Logging**: Structured events for all lifecycle phases

### Best Practices

1. **Always use output_model**: Validate returned data shape
2. **Use strict policies for untrusted code**: Start with STRICT_POLICY
3. **Enable debug mode during development**: Inspect tool calls and output
4. **Implement tool result validation**: Don't trust tool outputs blindly
5. **Use read-only permissions when possible**: Prefer FilePermission.READ
6. **Monitor resource metrics**: Track exceeded limits
7. **Implement human approval for sensitive tools**: Use snapshot resume pattern

### Filesystem Security

```python
# BAD: Overly permissive
fs = memory_filesystem(
    files={"/data/secrets.txt": "password123"},
    permissions={"/": FilePermission.WRITE},  # Everything writable!
)

# GOOD: Principle of least privilege
fs = memory_filesystem(
    files={
        "/data/public.txt": "public data",
        "/data/secrets.txt": "password123",
    },
    permissions={
        "/data/public.txt": FilePermission.READ,
        "/output": FilePermission.WRITE,
    },
    default_permission=FilePermission.DENY,
)
```

### Tool Security

```python
# BAD: Unchecked tool
def dangerous_tool(command: Any) -> str:
    import subprocess
    return subprocess.check_output(command, shell=True)

# GOOD: Type-checked, validated tool
def safe_calculator(operation: str, a: float, b: float) -> float:
    if operation not in ["add", "subtract", "multiply", "divide"]:
        raise ValueError(f"Unknown operation: {operation}")
    if operation == "add":
        return a + b
    elif operation == "subtract":
        return a - b
    elif operation == "multiply":
        return a * b
    elif operation == "divide":
        if b == 0:
            raise ValueError("Division by zero")
        return a / b
```

---

## Best Practices

### 1. Model Design

```python
# Define clear, specific models
class UserQuery(BaseModel):
    user_id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    query_text: str = Field(max_length=1000)
    filters: dict[str, str] = Field(default_factory=dict)

class SearchResult(BaseModel):
    results: list[str]
    count: int
    query_time_ms: float
```

### 2. Resource Limits

```python
# Start conservative, measure, then adjust
ctx = MontyContext(
    InputModel,
    policy="strict",  # Start strict
    metrics=MetricsCollector(),  # Track actual usage
)

# After analysis, create custom policy
CUSTOM_POLICY = ResourcePolicy(
    name="custom",
    guard=ResourceGuard(
        max_duration_secs=1.2,  # Based on p95 observed
        max_memory=24 * 1024 * 1024,  # Based on max observed + buffer
    )
)
```

### 3. Error Handling

```python
# Distinguish between user errors and system errors
try:
    result = ctx.execute(code, inputs)
except GrailValidationError as e:
    # User provided bad input
    return {"error": "Invalid input", "details": str(e)}
except GrailLimitError as e:
    # Code too complex or infinite loop
    return {"error": "Execution limit exceeded", "details": str(e)}
except GrailExecutionError as e:
    # Code has bugs
    return {"error": "Execution failed", "details": str(e)}
except Exception as e:
    # System error
    logger.error("Unexpected error", exc_info=e)
    return {"error": "Internal error"}
```

### 4. Tool Design

```python
# Keep tools focused and validated
class EmailTool:
    def __init__(self, allowed_domains: list[str]):
        self.allowed_domains = allowed_domains

    def send_email(self, to: str, subject: str, body: str) -> str:
        # Validate inputs
        if "@" not in to:
            raise ValueError("Invalid email address")
        domain = to.split("@")[1]
        if domain not in self.allowed_domains:
            raise ValueError(f"Domain {domain} not allowed")
        if len(subject) > 200:
            raise ValueError("Subject too long")

        # Actual implementation
        # ...
        return "Email sent"

email_tool = EmailTool(allowed_domains=["example.com"])
ctx = MontyContext(InputModel, tools=[email_tool.send_email])
```

### 5. Testing

```python
# Test with contract fixtures
def test_execution_with_limits():
    ctx = MontyContext(
        InputModel,
        limits={"max_recursion_depth": 50},
        output_model=OutputModel,
    )

    # Test success case
    result = ctx.execute("{'value': 42}", {})
    assert result.value == 42

    # Test limit exceeded
    with pytest.raises(GrailLimitError):
        ctx.execute("def f(n): return f(n+1)\nf(0)", {})

    # Test invalid output
    with pytest.raises(GrailOutputValidationError):
        ctx.execute("{'wrong_field': 42}", {})
```

### 6. Production Deployment

```python
# Use resilience features
metrics = MetricsCollector()
logger = StructuredLogger(sink=lambda e: structured_log.info(e))

ctx = MontyContext(
    InputModel,
    output_model=OutputModel,
    policy="ai_agent",
    metrics=metrics,
    logger=logger,
)

result = ctx.execute_with_resilience(
    code,
    inputs,
    retry_policy=RetryPolicy(
        attempts=3,
        backoff_seconds=0.5,
        retry_on=(GrailLimitError,)
    ),
    fallback=OutputModel(value=0)  # Safe default
)

# Monitor metrics
if metrics.snapshot()["counters"].get("executions_fallback", 0) > 0:
    alert("High fallback rate detected")
```

---

## Examples

### Example 1: Calculator with Tools

```python
from pydantic import BaseModel
from grail import MontyContext

class CalculatorInput(BaseModel):
    expression: str

class CalculatorOutput(BaseModel):
    result: float

def add(a: float, b: float) -> float:
    return a + b

def multiply(a: float, b: float) -> float:
    return a * b

ctx = MontyContext(
    CalculatorInput,
    output_model=CalculatorOutput,
    tools=[add, multiply]
)

result = ctx.execute(
    "{'result': multiply(add(2.0, 3.0), 4.0)}",
    {"expression": "(2 + 3) * 4"}
)
print(result.result)  # 20.0
```

### Example 2: Data Processing Pipeline

```python
from pydantic import BaseModel, Field
from grail import MontyContext, memory_filesystem, FilePermission

class DataInput(BaseModel):
    file_path: str

class DataOutput(BaseModel):
    lines: int
    word_count: int

fs = memory_filesystem(
    files={
        "/data/input.txt": "Hello world\nThis is a test\nGoodbye world"
    },
    permissions={
        "/data": FilePermission.READ,
    }
)

ctx = MontyContext(
    DataInput,
    output_model=DataOutput,
    filesystem=fs
)

result = ctx.execute("""
from pathlib import Path
content = Path(inputs['file_path']).read_text()
lines = content.split('\\n')
words = content.split()
{'lines': len(lines), 'word_count': len(words)}
""", {"file_path": "/data/input.txt"})

print(f"Lines: {result.lines}, Words: {result.word_count}")
```

### Example 3: AI Agent with Approval

```python
import asyncio
from pydantic import BaseModel
from grail import MontyContext

class AgentInput(BaseModel):
    task: str

class AgentOutput(BaseModel):
    result: str

def search_web(query: str) -> str:
    return f"Results for: {query}"

def send_email(to: str, subject: str) -> str:
    return f"Email sent to {to}"

async def run_agent_with_approval():
    ctx = MontyContext(
        AgentInput,
        output_model=AgentOutput,
        tools=[search_web, send_email]
    )

    code = """
query_result = search_web(inputs['task'])
email_result = send_email('user@example.com', 'Results')
{'result': f'{query_result} | {email_result}'}
"""

    snapshot = await ctx.start(code, {"task": "python tutorials"})

    while not snapshot.is_complete:
        print(f"\nTool call: {snapshot.function_name}")
        print(f"Args: {snapshot.args}")

        approved = input("Approve? (y/n): ") == "y"
        if not approved:
            return {"result": "Rejected by user"}

        # Execute approved tool
        result = await ctx.tools.invoke(
            snapshot.function_name,
            *snapshot.args,
            **snapshot.kwargs
        )
        snapshot = snapshot.resume(result)

    return snapshot.final_value

result = asyncio.run(run_agent_with_approval())
print(result)
```

### Example 4: Using @secure Decorator

```python
from pydantic import BaseModel
from grail import secure, ResourceGuard

class Point(BaseModel):
    x: float
    y: float

class Distance(BaseModel):
    value: float
    unit: str

@secure(
    limits={"max_duration_secs": 0.1},
)
def calculate_distance(p1: Point, p2: Point) -> Distance:
    import math
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    distance = math.sqrt(dx*dx + dy*dy)
    return Distance(value=distance, unit="units")

result = calculate_distance(
    Point(x=0, y=0),
    Point(x=3, y=4)
)
print(f"Distance: {result.value} {result.unit}")  # 5.0 units
```

### Example 5: Production Monitoring

```python
from grail import (
    MontyContext,
    MetricsCollector,
    StructuredLogger,
    RetryPolicy,
    GrailLimitError
)

# Setup observability
events = []
metrics = MetricsCollector()
logger = StructuredLogger(sink=lambda e: events.append(e))

ctx = MontyContext(
    InputModel,
    output_model=OutputModel,
    policy="ai_agent",
    metrics=metrics,
    logger=logger,
    debug=True
)

# Execute with resilience
result = ctx.execute_with_resilience(
    code,
    inputs,
    retry_policy=RetryPolicy(
        attempts=3,
        backoff_seconds=0.5,
        retry_on=(GrailLimitError,)
    ),
    fallback=OutputModel(default="fallback")
)

# Analyze
stats = metrics.snapshot()
print(f"Execution time: {stats['timings']['execute_async']['avg_ms']} ms")
print(f"Success: {stats['counters'].get('executions_success', 0)}")
print(f"Failed: {stats['counters'].get('executions_failed', 0)}")
print(f"Fallback used: {stats['counters'].get('executions_fallback', 0)}")

# Check debug payload
if ctx.debug:
    print(f"Tool calls: {len(ctx.debug_payload['tool_calls'])}")
    print(f"Stdout: {ctx.debug_payload['stdout']}")
    print(f"Resource metrics: {ctx.debug_payload['resource_metrics']}")
```

### Example 6: Multi-Policy Composition

```python
from grail import (
    ResourcePolicy,
    ResourceGuard,
    resolve_policy,
    MontyContext
)

# Define organization policies
ORG_BASE = ResourcePolicy(
    name="org_base",
    guard=ResourceGuard(
        max_duration_secs=2.0,
        max_memory=32 * 1024 * 1024,
    )
)

FINANCE_POLICY = ResourcePolicy(
    name="finance",
    inherits=("org_base",),
    guard=ResourceGuard(
        max_duration_secs=5.0,  # Override for complex calculations
    )
)

# Use multiple policies (strictest wins)
ctx = MontyContext(
    InputModel,
    policy=[FINANCE_POLICY, "strict"],
    available_policies={
        "org_base": ORG_BASE,
        "finance": FINANCE_POLICY
    }
)
# Effective limits: strict's 0.5s duration (strictest), strict's 8MB memory
```

---

## Migration from Direct Monty Usage

### Before (Direct Monty)

```python
from pydantic_monty import Monty, run_monty_async

# Manual serialization
inputs = {"a": 10, "b": 20}

# Manual type stubs
type_stubs = """
from typing import TypedDict
class InputModel(TypedDict):
    a: int
    b: int
"""

# Manual tool wiring
def add(x: int, y: int) -> int:
    return x + y

code = "add(inputs['a'], inputs['b'])"

# Create and run
runner = Monty(code, type_definitions=type_stubs)
result = await run_monty_async(
    runner,
    inputs={"inputs": inputs},
    globals={"add": add}
)
```

### After (Grail)

```python
from pydantic import BaseModel
from grail import MontyContext

class InputModel(BaseModel):
    a: int
    b: int

def add(x: int, y: int) -> int:
    return x + y

ctx = MontyContext(InputModel, tools=[add])
result = ctx.execute("add(inputs['a'], inputs['b'])", {"a": 10, "b": 20})
```

**Benefits:**
- Automatic input/output validation
- Automatic type stub generation
- Simplified API
- Built-in resource limits
- Structured logging and metrics
- Error normalization

---

## Appendix: Type Reference

### ResourceLimits (TypedDict)

```python
{
    "max_allocations": int,       # Optional
    "max_duration_secs": float,   # Optional
    "max_memory": int,            # Optional
    "gc_interval": int,           # Optional
    "max_recursion_depth": int,   # Optional
}
```

### DebugPayload (TypedDict)

```python
{
    "events": list[str],
    "stdout": str,
    "stderr": str,
    "tool_calls": list[DebugToolCall],
    "resource_metrics": dict[str, Any],
}
```

### DebugToolCall (TypedDict)

```python
{
    "name": str,
    "args": list[Any],
    "kwargs": dict[str, Any],
    "result": Any,
}
```

### PolicySpec (Type Alias)

```python
PolicySpec = str | ResourcePolicy
```

---

## Conclusion

Grail provides a production-ready, type-safe interface for executing untrusted Python code in isolated environments. By leveraging Pydantic models, automatic type stub generation, and flexible resource controls, it makes secure code execution accessible and ergonomic for Python developers.

For issues, contributions, or questions, visit the [GitHub repository](https://github.com/Bullish-Design/grail).
