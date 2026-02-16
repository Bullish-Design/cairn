# Grail Specification

## Table of Contents

1. [Introduction](#1-introduction)
2. [Public API](#2-public-api)
3. [.pym File Format](#3-pym-file-format)
4. [CLI Specification](#4-cli-specification)
5. [Artifact Specification](#5-artifact-specification)
6. [Error Specification](#6-error-specification)
7. [Type Checking Specification](#7-type-checking-specification)
8. [Resource Limits Specification](#8-resource-limits-specification)
9. [Filesystem Specification](#9-filesystem-specification)
10. [Snapshot/Resume Specification](#10-snapshotresume-specification)

---

## 1. Introduction

Grail v2 is a Python library that provides a transparent, first-class programming experience for Monty (a secure Python-like interpreter written in Rust). Grail's purpose is to eliminate friction when writing code for Monty while maintaining visibility into Monty's limitations.

### Goals

- **Transparency**: Make Monty's limitations visible and manageable
- **Minimalism**: ~15 public symbols, everything else is implementation detail
- **Developer Experience**: Full IDE support for Monty code via `.pym` files
- **Safety**: Pre-flight validation catches Monty incompatibilities before runtime
- **Inspectability**: All generated artifacts visible in `.grail/` directory

### Non-Goals

- Generic observability (logging, metrics, retries)
- Complex abstraction layers over Monty
- Universal sandbox solutions (use Monty directly for those)
- Enterprise policy composition systems

---

## 2. Public API

### 2.1 Core Functions

#### `grail.load(path, **options) -> GrailScript`

Load and parse a `.pym` file.

**Parameters**:
- `path` (`str | Path`): Path to the `.pym` file (required)
- `limits` (`dict | None`): Resource limits (default: `None`)
- `files` (`dict[str, str | bytes] | None`): Virtual filesystem files (default: `None`)
- `grail_dir` (`str | Path | None`): Directory for generated artifacts (default: `".grail"`, `None` disables)

**Returns**: `GrailScript` instance

**Raises**:
- `FileNotFoundError`: If `.pym` file doesn't exist
- `grail.ParseError`: If file has syntax errors
- `grail.CheckError`: If `@external` or `Input()` declarations are malformed

**Example**:
```python
import grail

script = grail.load("analysis.pym", limits={"max_memory": "16mb"})
```

#### `grail.run(code, inputs) -> Any`

Execute inline Monty code (escape hatch for simple cases).

**Parameters**:
- `code` (`str`): Monty code to execute (required)
- `inputs` (`dict[str, Any]`): Input values (default: `{}`)

**Returns**: Result of final expression in code

**Example**:
```python
import grail

result = await grail.run("x + y", inputs={"x": 1, "y": 2})
# result == 3
```

**Note**: This is intentionally minimal — no externals, no type checking, no artifact generation. For complex scripts, use `.pym` files.

### 2.2 GrailScript Class

#### Properties

| Property | Type | Description |
|---|---|---|
| `path` | `Path` | Original `.pym` file path |
| `name` | `str` | Script name (stem of filename) |
| `externals` | `dict[str, ExternalSpec]` | Extracted external function specs |
| `inputs` | `dict[str, InputSpec]` | Extracted input specs |
| `monty_code` | `str` | The processed code string for Monty |
| `stubs` | `str` | Generated type stub string |
| `limits` | `dict` | Active resource limits |

#### Methods

##### `await script.run(inputs, externals, **kwargs) -> Any`

Execute the script in Monty and return the result.

**Parameters**:
- `inputs` (`dict[str, Any]`): Input values (default: `{}`)
- `externals` (`dict[str, Callable]`): External function implementations (default: `{}`)
- `output_model` (`type[BaseModel] | None`): Optional Pydantic model to validate return value (default: `None`)
- `files` (`dict | None`): Override files from `load()` (default: `None`)
- `limits` (`dict | None`): Override limits from `load()` (default: `None`)

**Behavior**:
- Validates all required inputs are provided
- Validates all declared externals have implementations
- Warns if extra inputs/externals are provided
- Calls `pydantic_monty.Monty()` with processed code and stubs
- Calls `pydantic_monty.run_monty_async()`
- Writes stdout/stderr to `.grail/<name>/run.log`
- Returns script's final expression value
- If `output_model` provided, validates result and returns model instance

**Raises**:
- `grail.InputError`: Missing required input or wrong type
- `grail.ExternalError`: Missing external function implementation
- `grail.ExecutionError`: Monty runtime error
- `grail.LimitError`: Resource limit exceeded
- `grail.OutputError`: Output validation failed

**Example**:
```python
result = await script.run(
    inputs={"budget_limit": 5000.0, "department": "Engineering"},
    externals={
        "get_team_members": get_team_members,
        "get_expenses": get_expenses,
        "get_custom_budget": get_custom_budget,
    },
)
```

##### `script.run_sync(inputs, externals, **kwargs) -> Any`

Synchronous wrapper around `run()`.

**Parameters**: Same as `run()`

**Returns**: Same as `run()`

**Example**:
```python
result = script.run_sync(
    inputs={"budget_limit": 5000.0},
    externals={"get_team_members": get_team_members},
)
```

##### `script.check() -> CheckResult`

Run validation checks programmatically (same as `grail check` CLI).

**Returns**: `CheckResult` with validation results

**Example**:
```python
result = script.check()
if not result.valid:
    for error in result.errors:
        print(f"{error.lineno}:{error.col_offset}: {error.code} {error.message}")
```

##### `script.start(inputs, externals) -> Snapshot`

Begin resumable execution (pause/resume pattern).

**Parameters**:
- `inputs` (`dict[str, Any]`): Input values
- `externals` (`dict[str, Callable]`): External function implementations

**Returns**: `Snapshot` object

**Example**:
```python
snapshot = script.start(
    inputs={"user_id": 42},
    externals={"fetch_data": fetch_data, "save_result": save_result},
)

while not snapshot.is_complete:
    name = snapshot.function_name
    args = snapshot.args
    kwargs = snapshot.kwargs
    result = await externals[name](*args, **kwargs)
    snapshot = snapshot.resume(return_value=result)

final_result = snapshot.value
```

### 2.3 Declarations (for `.pym` files)

#### `grail.external`

Decorator to declare external functions in `.pym` files.

**Usage**:
```python
from grail import external

@external
async def fetch_data(url: str) -> dict[str, Any]:
    """Fetch data from URL."""
    ...
```

**Requirements**:
- Complete type annotations on parameters and return type
- Function body must be `...` (Ellipsis)
- Can be `async def` or `def`

#### `grail.Input(name, default=...)`

Declare input variables in `.pym` files.

**Usage**:
```python
from grail import Input

budget_limit: float = Input("budget_limit")
department: str = Input("department", default="Engineering")
```

**Parameters**:
- `name` (`str`): Input variable name
- `default` (`Any | None`): Optional default value

**Requirements**:
- Must have type annotation

### 2.4 Snapshot Class

#### Properties

| Property | Type | Description |
|---|---|---|
| `function_name` | `str` | Name of function being called |
| `args` | `tuple[Any, ...]` | Positional arguments |
| `kwargs` | `dict[str, Any]` | Keyword arguments |
| `is_complete` | `bool` | Whether execution is finished |
| `call_id` | `int` | Unique identifier for this call |

#### Methods

##### `snapshot.resume(**kwargs) -> Snapshot | Any`

Resume execution with return value or exception.

**Parameters**:
- `return_value` (`Any`): Value to return from external function
- `exception` (`BaseException`): Exception to raise in Monty

**Returns**: New `Snapshot` if more calls pending, final result if complete

**Example**:
```python
# Return value
snapshot = snapshot.resume(return_value=some_result)

# Raise exception
snapshot = snapshot.resume(exception=ValueError("error"))
```

##### `snapshot.dump() -> bytes`

Serialize snapshot to bytes.

**Returns**: Serialized snapshot data

**Example**:
```python
data = snapshot.dump()
```

##### `Snapshot.load(data) -> Snapshot` (static)

Deserialize snapshot from bytes.

**Parameters**:
- `data` (`bytes`): Serialized snapshot data

**Returns**: Restored `Snapshot` instance

**Example**:
```python
snapshot = Snapshot.load(data)
```

### 2.5 Resource Limits Presets

```python
grail.STRICT = {
    "max_memory": "8mb",
    "max_duration": "500ms",
    "max_recursion": 120,
}

grail.DEFAULT = {
    "max_memory": "16mb",
    "max_duration": "2s",
    "max_recursion": 200,
}

grail.PERMISSIVE = {
    "max_memory": "64mb",
    "max_duration": "5s",
    "max_recursion": 400,
}
```

### 2.6 Error Types

```python
grail.GrailError (base)
├── grail.ParseError
├── grail.CheckError
├── grail.InputError
├── grail.ExternalError
├── grail.ExecutionError
│   └── grail.LimitError
└── grail.OutputError
```

### 2.7 Check Result Types

```python
@dataclass
class grail.CheckMessage:
    code: str                      # "E001", "W001", etc.
    lineno: int
    col_offset: int
    end_lineno: int | None
    end_col_offset: int | None
    severity: Literal['error', 'warning']
    message: str
    suggestion: str | None

@dataclass
class grail.CheckResult:
    file: str
    valid: bool
    errors: list[CheckMessage]
    warnings: list[CheckMessage]
    info: dict[str, Any]
```

---

## 3. .pym File Format

### 3.1 Overview

`.pym` (Python for Monty) files are valid Python files intended to run inside Monty. IDEs treat them as Python with full syntax highlighting, autocomplete, and type checking.

### 3.2 Syntax

```python
# analysis.pym

from grail import external, Input

# --- Declarations Section ---
# These are metadata markers that grail tooling reads.

budget_limit: float = Input("budget_limit")
department: str = Input("department", default="Engineering")

@external
async def get_team_members(department: str) -> dict[str, Any]:
    """Get list of team members for a department."""
    ...

@external
async def get_expenses(user_id: int, quarter: str, category: str) -> dict[str, Any]:
    """Get expense line items for a user."""
    ...

# --- Executable Section ---
# Everything below is the actual Monty script.

team_data = await get_team_members(department=department)
members = team_data.get("members", [])

# ... rest of script ...

{
    "analyzed": len(members),
    "over_budget_count": len(over_budget),
    "details": over_budget,
}
```

### 3.3 Rules

1. **MUST** be syntactically valid Python 3.10+
2. `@external` functions **MUST** have complete type annotations (parameters + return)
3. `@external` function bodies **MUST** be `...` (Ellipsis)
4. `Input()` declarations **MUST** have a type annotation
5. All imports except `from grail import ...` and `from typing import ...` are forbidden
6. File's return value is its final expression (like a Jupyter cell)

### 3.4 Supported Python Features

- Functions and closures
- Async/await
- Comprehensions (list, dict, set, generator expressions)
- Basic data structures (int, float, str, bool, list, dict, tuple, set, None)
- Control flow (if/elif/else, for, while, try/except/finally)
- F-strings
- Type annotations

### 3.5 Unsupported Python Features

- Classes (deferred until Monty supports them)
- Generators and `yield`
- `with` statements
- `match` statements (deferred until Monty supports them)
- Lambda expressions (deferred until Monty supports them)
- Imports beyond `grail` and `typing`
- Most of the standard library

---

## 4. CLI Specification

### 4.1 Commands

#### `grail init`

Initialize a project for grail usage.

**Usage**:
```bash
grail init
```

**Creates**:
- `.grail/` directory
- Adds `.grail/` to `.gitignore` (if exists)
- Creates sample `.pym` file
- Prints getting-started message

#### `grail check [files...]`

Validate `.pym` files against Monty's constraints.

**Usage**:
```bash
# Check all .pym files in current directory (recursive)
grail check

# Check specific files
grail check analysis.pym sentiment.pym

# JSON output (for CI integration)
grail check --format json

# Strict mode — warnings become errors
grail check --strict
```

**Validates**:

| Check | Code | Severity | Example |
|---|---|---|---|
| Class definitions | E001 | Error | `class Foo: ...` |
| Generator/yield | E002 | Error | `def gen(): yield 1` |
| `with` statements | E003 | Error | `with open(f): ...` |
| `match` statements | E004 | Error | `match x: ...` |
| Forbidden imports | E005 | Error | `import json` |
| Missing type annotations on `@external` | E006 | Error | Parameters and return type required |
| `@external` with non-ellipsis body | E007 | Error | Body must be `...` |
| `Input()` without type annotation | E008 | Error | Type required |
| Monty type checker errors | E1xx | Error | From `ty` |
| Bare dict/list as return value | W001 | Warning | Consider naming for clarity |
| Unused `@external` function | W002 | Warning | Declared but never called |
| Unused `Input()` variable | W003 | Warning | Declared but never referenced |
| Very long script (>200 lines) | W004 | Warning | May indicate too much logic |

**Output**:
```
analysis.pym: OK (3 externals, 2 inputs, 0 errors, 1 warning)
sentiment.pym: FAIL
  sentiment.pym:12:1: E001 Class definitions are not supported in Monty
  sentiment.pym:25:5: E003 'with' statements are not supported in Monty

Checked 2 files: 1 passed, 1 failed
```

#### `grail run <file.pym> [--host <host.py>]`

Execute a `.pym` file.

**Usage**:
```bash
# Run with a host file
grail run analysis.pym --host host.py

# Run with inline inputs
grail run analysis.pym --host host.py --input budget_limit=5000
```

**Host File Format**:
```python
# host.py
from grail import load
from data import get_team_members, get_expenses, get_custom_budget

async def main():
    script = load("analysis.pym")
    result = await script.run(
        inputs={"budget_limit": 5000.0, "department": "Engineering"},
        externals={
            "get_team_members": get_team_members,
            "get_expenses": get_expenses,
            "get_custom_budget": get_custom_budget,
        },
    )
    print(result)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

#### `grail watch [dir]`

File watcher that re-runs `grail check` on `.pym` file changes.

**Usage**:
```bash
# Watch current directory
grail watch

# Watch specific directory
grail watch src/scripts/
```

#### `grail clean`

Remove the `.grail/` directory.

**Usage**:
```bash
grail clean
```

---

## 5. Artifact Specification

### 5.1 Directory Structure

```
.grail/
├── <script_name>/
│   ├── stubs.pyi       # Generated type stubs
│   ├── check.json      # Validation results
│   ├── externals.json  # External function specs
│   ├── inputs.json     # Input declarations
│   ├── monty_code.py  # Stripped Monty code
│   └── run.log        # Execution output
```

### 5.2 stubs.pyi

Type stubs sent to Monty's `ty` type checker.

```python
# .grail/analysis/stubs.pyi
# Auto-generated by grail — do not edit
from typing import Any

budget_limit: float
department: str

async def get_team_members(department: str) -> dict[str, Any]:
    """Get list of team members for a department."""
    ...

async def get_expenses(user_id: int, quarter: str, category: str) -> dict[str, Any]:
    """Get expense line items for a user."""
    ...

async def get_custom_budget(user_id: int) -> dict[str, Any] | None:
    """Get custom budget for a user if they have one."""
    ...
```

### 5.3 check.json

Results of `grail check`.

```json
{
  "file": "analysis.pym",
  "valid": true,
  "errors": [],
  "warnings": [
    {
      "line": 34,
      "column": 4,
      "code": "W001",
      "message": "Bare dict as return value — consider assigning to a variable for clarity"
    }
  ],
  "info": {
    "externals_count": 3,
    "inputs_count": 2,
    "lines_of_code": 28,
    "monty_features_used": ["async_await", "for_loop", "list_comprehension", "f_string"]
  }
}
```

### 5.4 externals.json

Machine-readable extraction of external function signatures.

```json
{
  "externals": [
    {
      "name": "get_team_members",
      "async": true,
      "parameters": [
        {"name": "department", "type": "str", "default": null}
      ],
      "return_type": "dict[str, Any]",
      "docstring": "Get list of team members for a department."
    }
  ]
}
```

### 5.5 inputs.json

Machine-readable extraction of input declarations.

```json
{
  "inputs": [
    {"name": "budget_limit", "type": "float", "required": true, "default": null},
    {"name": "department", "type": "str", "required": false, "default": "Engineering"}
  ]
}
```

### 5.6 monty_code.py

Actual Python code sent to Monty interpreter.

```python
# .grail/analysis/monty_code.py
# Auto-generated by grail — this is what Monty actually executes

team_data = await get_team_members(department=department)
members = team_data.get("members", [])
# ... rest of executable code ...
```

### 5.7 run.log

Combined stdout/stderr from execution.

```
[grail] Running analysis.pym with 2 inputs, 3 externals
[stdout] Processing 5 team members...
[grail] Completed in 0.042ms, return value: dict (3 keys)
```

---

## 6. Error Specification

### 6.1 Error Hierarchy

```
grail.GrailError (base)
├── grail.ParseError          # .pym file has syntax errors
├── grail.CheckError          # @external or Input() declarations are malformed
├── grail.InputError          # missing/invalid input at runtime
├── grail.ExternalError       # missing external function implementation
├── grail.ExecutionError      # Monty runtime error
│   └── grail.LimitError      # resource limit exceeded
└── grail.OutputError         # output validation failed
```

### 6.2 Error Format

All errors reference the original `.pym` file, not generated `monty_code.py`. Grail maintains a source map between the two.

**Example**:
```
grail.ExecutionError: analysis.pym:22 — NameError: name 'undefined_var' is not defined

  20 |     total = sum(item["amount"] for item in items)
  21 |
> 22 |     if total > undefined_var:
  23 |         custom = await get_custom_budget(user_id=uid)

Context: This variable is not defined in the script and is not a declared Input().
```

### 6.3 Error Descriptions

#### ParseError

Raised when the `.pym` file has Python syntax errors.

**Example**:
```python
try:
    script = grail.load("invalid.pym")
except grail.ParseError as e:
    print(f"Syntax error at line {e.lineno}: {e.message}")
```

#### CheckError

Raised when `@external` or `Input()` declarations are malformed.

**Triggers**:
- Missing type annotations on `@external` function
- `@external` function body is not `...`
- `Input()` call without type annotation

#### InputError

Raised when runtime inputs don't match declared `Input()` specs.

**Triggers**:
- Missing required input (no default)
- Input type doesn't match declared type

#### ExternalError

Raised when external functions aren't provided or don't match declarations.

**Triggers**:
- External function declared but not provided at runtime
- Extra external function provided (not in `@external` declarations)

#### ExecutionError

Raised when Monty runtime error occurs.

**Triggers**:
- NameError, TypeError, ValueError, etc.
- Monty-specific errors

**Includes**:
- Line/column numbers mapped to `.pym` file
- Surrounding code context
- Full Monty traceback in `.grail/<name>/run.log`

#### LimitError

Raised when resource limits are exceeded.

**Triggers**:
- Memory limit exceeded
- Duration limit exceeded
- Recursion depth exceeded

#### OutputError

Raised when output validation against `output_model` fails.

**Triggers**:
- Return value doesn't match Pydantic model schema

---

## 7. Type Checking Specification

### 7.1 How It Works

1. Developer writes `@external` functions with full type annotations in `.pym` file
2. `grail.load()` parses these with `ast` and generates `.pyi` stubs
3. Stubs are passed to Monty's built-in `ty` type checker
4. `grail check` reports type errors before execution

### 7.2 What Gets Generated

**From `.pym` code**:
```python
from grail import external, Input
from typing import Any

budget: float = Input("budget")

@external
async def get_data(id: int) -> dict[str, Any]:
    """Fetch data by ID."""
    ...
```

**Grail generates**:
```python
# .grail/script_name/stubs.pyi
from typing import Any

budget: float

async def get_data(id: int) -> dict[str, Any]:
    """Fetch data by ID."""
    ...
```

### 7.3 Supported Types

Stubs support all types that Monty's `ty` checker understands:

- Primitives: `int`, `float`, `str`, `bool`, `None`
- Collections: `list[T]`, `dict[K, V]`, `tuple[T, ...]`, `set[T]`
- Unions: `T | None`, `int | str`
- `Any`
- Nested combinations of the above

### 7.4 IDE Integration

Because `.pym` files are valid Python and `@external` functions have real signatures, IDEs provide:
- Autocomplete for external function parameters
- Type error highlighting on argument mismatches
- Hover documentation from docstrings
- Go-to-definition (lands on the `@external` declaration)

The `grail` package ships with `py.typed` marker and type stubs for `external`, `Input`, etc.

---

## 8. Resource Limits Specification

### 8.1 Design

One level, one dict. No policies, no inheritance, no composition.

```python
script = grail.load("analysis.pym", limits={
    "max_memory": "16mb",
    "max_duration": "5s",
    "max_recursion": 200,
})
```

### 8.2 Available Limits

| Key | Type | Default | Description |
|---|---|---|---|
| `max_memory` | `str | int` | `"16mb"` | Maximum memory. String accepts `"16mb"`, `"1gb"`. Int is bytes. |
| `max_duration` | `str | float` | `"2s"` | Maximum execution time. String accepts `"500ms"`, `"2s"`. Float is seconds. |
| `max_recursion` | `int` | `200` | Maximum recursion depth |
| `max_allocations` | `int | None` | `None` | Maximum allocation count (advanced) |

### 8.3 String Format Parsing

**Memory**: `"16mb"` → `16 * 1024 * 1024`, `"1gb"` → `1 * 1024 * 1024 * 1024`

**Duration**: `"500ms"` → `0.5`, `"2s"` → `2.0`, `"1.5s"` → `1.5`

**Case insensitive**: `"16MB"`, `"16Mb"`, `"16mb"` all work

### 8.4 Named Presets

```python
import grail

script = grail.load("analysis.pym", limits=grail.STRICT)
script = grail.load("analysis.pym", limits=grail.DEFAULT)
script = grail.load("analysis.pym", limits=grail.PERMISSIVE)
```

| Preset | Memory | Duration | Recursion |
|---|---|---|---|
| `grail.STRICT` | 8mb | 500ms | 120 |
| `grail.DEFAULT` | 16mb | 2s | 200 |
| `grail.PERMISSIVE` | 64mb | 5s | 400 |

Presets are plain dicts. No classes, no methods, no inheritance.

### 8.5 Override at Runtime

```python
# Load with defaults, override per-run
script = grail.load("analysis.pym")
result = await script.run(
    inputs={...},
    externals={...},
    limits={"max_duration": "10s"},  # override just this one
)
```

---

## 9. Filesystem Specification

### 9.1 Design

Dict in, Monty `OSAccess` out.

```python
script = grail.load("analysis.pym", files={
    "/data/customers.csv": Path("customers.csv").read_text(),
    "/data/tweets.json": Path("tweets.json").read_text(),
})
```

This maps directly to Monty's `MemoryFile` + `OSAccess`.

### 9.2 In the `.pym` File

```python
# analysis.pym
from pathlib import Path

content = open(Path("/data/customers.csv")).read()
# or whatever file API Monty supports
```

### 9.3 Dynamic Files

If you need files determined at runtime, pass them to `run()`:

```python
result = await script.run(
    inputs={...},
    externals={...},
    files={"/data/report.csv": generate_csv()},
)
```

### 9.4 No Custom Hooks

If you need dynamic file behavior (e.g., lazily loading files from S3), make it an external function:

```python
# In the .pym file:
@external
async def read_file(path: str) -> str:
    """Read a file from storage."""
    ...

content = await read_file("/data/customers.csv")
```

This is more explicit and doesn't require filesystem abstraction layers.

---

## 10. Snapshot/Resume Specification

### 10.1 Design

Thin wrapper over Monty's native snapshot mechanism.

### 10.2 Usage Pattern

```python
script = grail.load("workflow.pym")

# Start execution — pauses when an external function is called
snapshot = script.start(
    inputs={"user_id": 42},
    externals={"fetch_data": fetch_data, "save_result": save_result},
)

# Execution loop
while not snapshot.is_complete:
    # The script called an external function — fulfill it
    name = snapshot.function_name
    args = snapshot.args
    kwargs = snapshot.kwargs

    result = await externals[name](*args, **kwargs)
    snapshot = snapshot.resume(return_value=result)

# Done
final_result = snapshot.value
```

### 10.3 Serialization

```python
# Serialize for storage/transfer
data = snapshot.dump()  # -> bytes

# Restore later
snapshot = grail.Snapshot.load(data)
result = snapshot.resume(return_value=some_result)
```

This is a direct pass-through to `pydantic_monty.MontySnapshot.dump()` / `.load()`. No base64 wrappers, no custom serialization.

---

## Appendix A: Migration from Grail v1

This is a clean break. No automated migration.

### For Grail v1 Users

1. **Convert code strings to `.pym` files** — move sandboxed code out of Python strings
2. **Replace `MontyContext` with `grail.load()`** — 13 constructor parameters become 3 optional kwargs
3. **Replace `ToolRegistry` with a plain dict** — `externals={"name": func}`
4. **Replace `GrailFilesystem` with a plain dict** — `files={"/path": content}`
5. **Replace resource policies with a limits dict** — `limits={"max_memory": "16mb"}`
6. **Remove observability code** — add your own `logging`/metrics at application level
7. **Remove `@secure` decorators** — use `.pym` files instead
8. **Run `grail check`** — catch Monty compatibility issues immediately

---

## Appendix B: Version Strategy

Grail v2 will be released as `grail >= 2.0.0` (or a rename, TBD). The `grail >= 0.x` / `1.x` line is frozen — no further development.

---

## Appendix C: Public API Summary

**Total: ~15 public symbols**

```python
# Core
grail.load(path, **options) -> GrailScript
grail.run(code, inputs) -> Any

# Declarations (for .pym files)
grail.external
grail.Input(name, default=...)

# Snapshots
grail.Snapshot
grail.Snapshot.load(data) -> Snapshot

# Limits
grail.STRICT
grail.DEFAULT
grail.PERMISSIVE

# Errors
grail.GrailError
grail.ParseError
grail.CheckError
grail.InputError
grail.ExternalError
grail.ExecutionError
grail.LimitError
grail.OutputError

# Check results
grail.CheckResult
grail.CheckMessage
```
