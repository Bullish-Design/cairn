# Grail: Complete Spec

## Executive Summary

Grail v2 is a clean-break redesign that makes Monty a **first-class, transparent programming experience**. Instead of hiding Monty behind enterprise abstractions (policy inheritance, filesystem hooks, observability pipelines), Grail v2 gives developers:

- **`.pym` files** — a dedicated file extension for Monty code, with full IDE support
- **`grail check`** — a CLI that validates `.pym` files against Monty's limitations *before* runtime
- **`.grail/` directory** — transparent, inspectable generated artifacts (stubs, validation results, logs)
- **A minimal host API** — `grail.load()` → `script.run()`, nothing more

The core principle: **Monty is a limited Python runtime, and Grail should make those limitations visible and manageable — not hide them behind abstractions.**

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [The `.pym` File Format](#2-the-pym-file-format)
3. [The `.grail/` Directory](#3-the-grail-directory)
4. [CLI Tooling](#4-cli-tooling)
5. [Host-Side Python API](#5-host-side-python-api)
6. [Resource Limits](#6-resource-limits)
7. [Filesystem Access](#7-filesystem-access)
8. [Type Checking & Stub Generation](#8-type-checking--stub-generation)
9. [Error Reporting](#9-error-reporting)
10. [Pause/Resume (Snapshots)](#10-pauseresume-snapshots)
11. [GrailModel (Deferred)](#11-grailmodel-deferred)
12. [What's Explicitly Removed](#12-whats-explicitly-removed)
13. [Package Structure](#13-package-structure)
14. [Migration Path](#14-migration-path)

---

## 1. Motivation

### What Monty Is

Monty is a minimal, secure Python interpreter written in Rust (~0.06ms startup). It exists for one purpose: safely executing AI-generated Python code. It supports a subset of Python — functions, closures, async/await, comprehensions, basic data structures — but **no classes** (yet), **no generators**, **no `with` statements**, and almost **no stdlib**. External functions are the sole bridge to the outside world.

### What Grail v1 Does Wrong

Grail v1 wraps Monty in 10+ modules exposing 37+ public symbols:

| Grail v1 Abstraction | Why It's Problematic |
|---|---|
| 4-layer resource policy inheritance with cycle detection | Configuration values don't need an object hierarchy |
| `GrailFilesystem` with hierarchical permissions, prefix hooks, callback files | Reimplements what Monty's `OSAccess` already does, adding complexity |
| `StructuredLogger` + `MetricsCollector` + `RetryPolicy` | Generic observability — not Monty-specific, belongs in user code |
| `@secure` decorator with model inference from source inspection | Clever but opaque; developers can't see what's happening |
| `StubGenerator` with recursive type walking and caching | Necessary work, but hidden from the developer |
| Late-import signature probing for API compatibility | Implementation detail that signals instability, not a feature |
| `MontyContext` with 13 constructor parameters | The "god object" that ties everything together |

A developer using Grail v1 has no idea what Monty code actually runs, what stubs were generated, or why their code failed. Everything is hidden behind abstractions that exist to make the API "clean" at the cost of making it **opaque**.

### What Developers Actually Need

From studying the raw Monty examples (expense_analysis, sql_playground), the real pain points are:

1. **Code as strings** — no IDE support (highlighting, autocomplete, error squiggles)
2. **Type stubs as strings** — manual duplication of function signatures, prone to drift
3. **Double declaration** — external functions listed by name in `Monty()` AND by implementation in `run_monty_async()`
4. **No pre-flight validation** — you discover Monty's limitations at runtime
5. **Invisible internals** — no way to inspect generated stubs or see what Monty received

Grail v2 solves exactly these problems. Nothing more.

---

## 2. The `.pym` File Format

### Overview

A `.pym` (Python for Monty) file is a **valid Python file** that is intended to run inside Monty. IDEs treat it as Python — full syntax highlighting, autocomplete, type checking. Grail tooling reads it to extract metadata and validate it against Monty's constraints.

### Syntax

```python
# analysis.pym

from grail import external, Input

# --- Declarations Section ---
# These are metadata markers that grail tooling reads.
# They are valid Python (the decorators/calls are real),
# but they execute as no-ops inside Monty.

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

@external
async def get_custom_budget(user_id: int) -> dict[str, Any] | None:
    """Get custom budget for a user if they have one."""
    ...

# --- Executable Section ---
# Everything below the declarations is the actual Monty script.

team_data = await get_team_members(department=department)
members = team_data.get("members", [])

STANDARD_BUDGET = budget_limit

over_budget = []
for member in members:
    uid = member["id"]
    name = member["name"]

    expenses_data = await get_expenses(user_id=uid, quarter="Q3", category="travel")
    items = expenses_data.get("items", [])
    total = sum(item["amount"] for item in items)

    if total > STANDARD_BUDGET:
        custom = await get_custom_budget(user_id=uid)
        actual_budget = custom["amount"] if custom else STANDARD_BUDGET

        if total > actual_budget:
            over_budget.append({
                "name": name,
                "spent": total,
                "budget": actual_budget,
                "over_by": total - actual_budget,
            })

# The final expression is the script's return value
{
    "analyzed": len(members),
    "over_budget_count": len(over_budget),
    "details": over_budget,
}
```

### Key Design Decisions

**Why `.pym` and not just `.py`?**
- Clear signal: "this file runs in Monty, not CPython"
- Enables file-type-specific linting rules (e.g., IDE plugins, CI checks)
- Prevents accidental execution with `python analysis.pym`
- The `.grail/` tooling can glob for `**/*.pym` without false positives

**Why `from grail import external, Input` and not magic comments?**
- It's valid Python — IDEs understand it
- `@external` decorated functions provide real type information to IDE type checkers
- `Input()` calls have a real return type, so `budget_limit: float` is understood by the IDE
- No custom parser needed — grail reads it with Python's own `ast` module

**What `@external` means:**
- Declares that this function is provided by the host at runtime
- The signature (name, parameters, return type, docstring) becomes the type stub
- The `...` body is never executed — it's a declaration, like a `.pyi` stub but inline
- Grail's tooling extracts these to generate Monty-compatible stubs automatically

**What `Input()` means:**
- Declares a named input variable that the host must provide at runtime
- The type annotation provides type checking in both IDE and Monty's `ty` checker
- Optional `default` parameter for inputs that aren't required
- At Monty runtime, this resolves to the value passed by the host

### `.pym` File Rules

1. A `.pym` file MUST be syntactically valid Python 3.10+
2. `@external` functions MUST have complete type annotations (parameters + return)
3. `@external` function bodies MUST be `...` (Ellipsis) — no implementation
4. `Input()` declarations MUST have a type annotation
5. All imports except `from grail import ...` and `from typing import ...` are forbidden
6. The file's return value is its final expression (like a Jupyter cell)

---

## 3. The `.grail/` Directory

### Overview

The `.grail/` directory contains all generated artifacts from grail tooling. It is:
- Auto-generated by `grail check` and `grail run`
- Inspectable for debugging
- Safe to delete (regenerated on next run)
- Should be added to `.gitignore`

### Structure

```
my_project/
├── analysis.pym
├── sentiment.pym
├── host.py
├── .gitignore              # includes .grail/
└── .grail/
    ├── analysis/
    │   ├── stubs.pyi       # Generated type stubs for Monty's type checker
    │   ├── check.json      # Validation results (errors, warnings, info)
    │   ├── externals.json  # Extracted external function signatures
    │   ├── inputs.json     # Extracted input declarations with types
    │   ├── monty_code.py   # The actual code string sent to Monty (post-processing)
    │   └── run.log         # stdout/stderr from last execution
    └── sentiment/
        ├── stubs.pyi
        ├── check.json
        ├── externals.json
        ├── inputs.json
        ├── monty_code.py
        └── run.log
```

### Artifact Details

#### `stubs.pyi`
The type stubs sent to Monty's `ty` type checker. Generated from `@external` declarations and `Input()` types.

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

#### `check.json`
Results of `grail check`, including Monty compatibility issues and type errors.

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

#### `externals.json`
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

#### `inputs.json`
Machine-readable extraction of input declarations.

```json
{
  "inputs": [
    {"name": "budget_limit", "type": "float", "required": true, "default": null},
    {"name": "department", "type": "str", "required": false, "default": "Engineering"}
  ]
}
```

#### `monty_code.py`
The actual Python code string that will be sent to the Monty interpreter. This is the `.pym` file with grail declarations stripped and transformed into plain Monty-compatible code.

```python
# .grail/analysis/monty_code.py
# Auto-generated by grail — this is what Monty actually executes

team_data = await get_team_members(department=department)
members = team_data.get("members", [])
# ... rest of executable code ...
```

#### `run.log`
Combined stdout/stderr from the most recent execution.

```
[grail] Running analysis.pym with 2 inputs, 3 externals
[stdout] Processing 5 team members...
[grail] Completed in 0.042ms, return value: dict (3 keys)
```

---

## 4. CLI Tooling

### Commands

#### `grail check [files...]`

Validates `.pym` files against Monty's constraints without executing them.

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

**What `grail check` validates:**

| Check | Code | Severity | Example |
|---|---|---|---|
| Class definitions | E001 | Error | `class Foo: ...` — not supported in Monty |
| Generator/yield | E002 | Error | `def gen(): yield 1` — not supported |
| `with` statements | E003 | Error | `with open(f): ...` — not supported |
| `match` statements | E004 | Error | `match x: ...` — not supported (yet) |
| Forbidden imports | E005 | Error | `import json` — not available in Monty |
| Missing type annotations on `@external` | E006 | Error | Parameters and return type required |
| `@external` with non-ellipsis body | E007 | Error | Body must be `...` |
| `Input()` without type annotation | E008 | Error | Type required for stub generation |
| Monty type checker errors | E1xx | Error | From `ty` — type mismatches, etc. |
| Bare dict/list as return value | W001 | Warning | Consider naming for clarity |
| Unused `@external` function | W002 | Warning | Declared but never called |
| Unused `Input()` variable | W003 | Warning | Declared but never referenced |
| Very long script (>200 lines) | W004 | Warning | May indicate too much logic in sandbox |

**Output:**

```
analysis.pym: OK (3 externals, 2 inputs, 0 errors, 1 warning)
sentiment.pym: FAIL
  sentiment.pym:12:1: E001 Class definitions are not supported in Monty
  sentiment.pym:25:5: E003 'with' statements are not supported in Monty
  sentiment.pym:31:0: E005 'import json' — module not available in Monty

Checked 2 files: 1 passed, 1 failed
```

#### `grail run <file.pym> [--host <host.py>]`

Executes a `.pym` file. The `--host` flag specifies a Python file that provides external function implementations and inputs.

```bash
# Run with a host file that provides externals and inputs
grail run analysis.pym --host host.py

# Run with inline inputs (externals must come from --host)
grail run analysis.pym --host host.py --input budget_limit=5000
```

The host file exports a standard interface (see [Section 5](#5-host-side-python-api)).

#### `grail init`

Initializes a project for grail usage.

```bash
grail init
```

Creates:
- `.grail/` directory
- Adds `.grail/` to `.gitignore` (if exists)
- Creates a sample `.pym` file
- Prints a getting-started message

#### `grail watch`

File watcher that re-runs `grail check` on `.pym` file changes.

```bash
grail watch

# Watch specific directory
grail watch src/scripts/
```

#### `grail clean`

Removes the `.grail/` directory.

```bash
grail clean
```

---

## 5. Host-Side Python API

### Core API: `grail.load()` + `script.run()`

```python
import grail

# Load a .pym file
script = grail.load("analysis.pym")

# Run it
result = await script.run(
    inputs={"budget_limit": 5000.0, "department": "Engineering"},
    externals={
        "get_team_members": my_get_team_members,
        "get_expenses": my_get_expenses,
        "get_custom_budget": my_get_custom_budget,
    },
)
```

That's the entire API for basic usage. Everything else is optional.

### `grail.load(path, **options) -> GrailScript`

Loads and parses a `.pym` file. Returns a `GrailScript` object.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str \| Path` | required | Path to the `.pym` file |
| `limits` | `dict \| None` | `None` | Resource limits (see [Section 6](#6-resource-limits)) |
| `files` | `dict[str, str \| bytes] \| None` | `None` | Virtual filesystem files (see [Section 7](#7-filesystem-access)) |
| `grail_dir` | `str \| Path \| None` | `".grail"` | Where to write generated artifacts. `None` disables artifact generation |

**What `load()` does:**

1. Reads the `.pym` file
2. Parses it with `ast` to extract `@external` declarations and `Input()` calls
3. Generates the stripped Monty code (removes grail imports and declarations)
4. Generates type stubs from extracted declarations
5. Writes artifacts to `.grail/<name>/` (stubs, externals, inputs, monty_code)
6. Returns a `GrailScript` ready for execution

**Errors on load:**
- `FileNotFoundError` if the `.pym` file doesn't exist
- `grail.ParseError` if the file has syntax errors
- `grail.CheckError` if `@external` or `Input()` declarations are malformed

### `GrailScript`

The loaded, validated, ready-to-run script object.

**Properties:**

| Property | Type | Description |
|---|---|---|
| `path` | `Path` | Original `.pym` file path |
| `name` | `str` | Script name (stem of filename) |
| `externals` | `dict[str, ExternalSpec]` | Extracted external function specs |
| `inputs` | `dict[str, InputSpec]` | Extracted input specs |
| `monty_code` | `str` | The processed code string for Monty |
| `stubs` | `str` | Generated type stub string |
| `limits` | `dict` | Active resource limits |

**Methods:**

#### `await script.run(inputs, externals, **kwargs) -> Any`

Executes the script in Monty and returns the result.

```python
result = await script.run(
    inputs={"budget_limit": 5000.0},
    externals={"get_team_members": my_func},
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `inputs` | `dict[str, Any]` | `{}` | Input values (must match `Input()` declarations) |
| `externals` | `dict[str, Callable]` | `{}` | External function implementations (must match `@external` declarations) |
| `output_model` | `type[BaseModel] \| None` | `None` | Optional Pydantic model to validate the return value |
| `files` | `dict \| None` | `None` | Override files from `load()` |

**Behavior:**
- Validates that all required inputs are provided (those without defaults)
- Validates that all declared externals have implementations
- Warns (to stderr) if extra inputs/externals are provided that weren't declared
- Calls `pydantic_monty.Monty()` with the processed code and stubs
- Calls `pydantic_monty.run_monty_async()` with inputs, externals, and filesystem
- Writes stdout/stderr to `.grail/<name>/run.log`
- Returns the script's final expression value
- If `output_model` is provided, validates the result and returns a model instance

**Errors on run:**
- `grail.InputError` — missing required input or wrong type
- `grail.ExternalError` — missing external function implementation
- `grail.ExecutionError` — Monty runtime error (includes line/column from Monty)
- `grail.LimitError` — resource limit exceeded (subclass of `ExecutionError`)
- `grail.OutputError` — output validation failed against `output_model`

#### `script.run_sync(inputs, externals, **kwargs) -> Any`

Synchronous wrapper around `run()` for convenience.

```python
result = script.run_sync(
    inputs={"budget_limit": 5000.0},
    externals={"get_team_members": my_func},
)
```

#### `script.check() -> CheckResult`

Runs validation checks programmatically (same as `grail check` CLI).

```python
result = script.check()
if not result.valid:
    for error in result.errors:
        print(f"{error.line}:{error.column}: {error.code} {error.message}")
```

#### `script.start(inputs, externals) -> Snapshot`

Begins resumable execution (pause/resume pattern). See [Section 10](#10-pauseresume-snapshots).

### Full Example

```python
# host.py
import grail
from data import get_team_members, get_expenses, get_custom_budget

async def analyze_expenses():
    script = grail.load("analysis.pym")

    result = await script.run(
        inputs={
            "budget_limit": 5000.0,
            "department": "Engineering",
        },
        externals={
            "get_team_members": get_team_members,
            "get_expenses": get_expenses,
            "get_custom_budget": get_custom_budget,
        },
    )

    print(f"Analyzed {result['analyzed']} members")
    print(f"{result['over_budget_count']} over budget")
    for detail in result["details"]:
        print(f"  {detail['name']}: ${detail['over_by']:.2f} over")

if __name__ == "__main__":
    import asyncio
    asyncio.run(analyze_expenses())
```

### Inline Code (Escape Hatch)

For cases where a `.pym` file is overkill (one-liner evaluation, dynamic code), `grail.run()` provides direct execution:

```python
import grail

result = await grail.run(
    "x + y",
    inputs={"x": 1, "y": 2},
)
# result == 3
```

This is intentionally minimal — no externals, no type checking, no artifact generation. For anything complex, use a `.pym` file.

---

## 6. Resource Limits

### Design: One Level, One Dict

No policies. No inheritance. No composition. No cycle detection.

```python
script = grail.load("analysis.pym", limits={
    "max_memory": "16mb",
    "max_duration": "5s",
    "max_recursion": 200,
})
```

### Available Limits

| Key | Type | Default | Description |
|---|---|---|---|
| `max_memory` | `str \| int` | `"16mb"` | Maximum memory. String accepts `"16mb"`, `"1gb"`. Int is bytes. |
| `max_duration` | `str \| float` | `"2s"` | Maximum execution time. String accepts `"500ms"`, `"2s"`. Float is seconds. |
| `max_recursion` | `int` | `200` | Maximum recursion depth |
| `max_allocations` | `int \| None` | `None` | Maximum allocation count (advanced) |

### Named Presets

For convenience, not for composition:

```python
script = grail.load("analysis.pym", limits=grail.STRICT)
script = grail.load("analysis.pym", limits=grail.PERMISSIVE)
```

| Preset | Memory | Duration | Recursion |
|---|---|---|---|
| `grail.STRICT` | 8mb | 500ms | 120 |
| `grail.DEFAULT` | 16mb | 2s | 200 |
| `grail.PERMISSIVE` | 64mb | 5s | 400 |

Presets are plain dicts. `grail.STRICT` is literally `{"max_memory": "8mb", "max_duration": "500ms", "max_recursion": 120}`. No classes, no methods, no inheritance.

### Override at Runtime

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

## 7. Filesystem Access

### Design: Dict In, Monty `OSAccess` Out

```python
script = grail.load("analysis.pym", files={
    "/data/customers.csv": Path("customers.csv").read_text(),
    "/data/tweets.json": Path("tweets.json").read_text(),
})
```

This maps directly to Monty's `MemoryFile` + `OSAccess`. No `GrailFilesystem`, no `FilePermission` enum, no hook system, no path traversal detection (Monty handles that).

### In the `.pym` File

```python
# analysis.pym
from pathlib import Path

content = open(Path("/data/customers.csv")).read()
# or whatever file API Monty supports
```

### Dynamic Files

If you need files determined at runtime, pass them to `run()`:

```python
result = await script.run(
    inputs={...},
    externals={...},
    files={"/data/report.csv": generate_csv()},
)
```

### No Custom Hooks

If you need dynamic file behavior (e.g., lazily loading files from S3), make it an external function instead:

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

## 8. Type Checking & Stub Generation

### How It Works

1. Developer writes `@external` functions with full type annotations in the `.pym` file
2. `grail.load()` parses these with `ast` and generates `.pyi` stubs
3. The stubs are passed to Monty's built-in `ty` type checker
4. `grail check` reports type errors before execution

### What Gets Generated

From this `.pym` code:

```python
from grail import external, Input
from typing import Any

budget: float = Input("budget")

@external
async def get_data(id: int) -> dict[str, Any]:
    """Fetch data by ID."""
    ...
```

Grail generates this stub:

```python
# .grail/script_name/stubs.pyi
from typing import Any

budget: float

async def get_data(id: int) -> dict[str, Any]:
    """Fetch data by ID."""
    ...
```

### Type Support

Stubs support all types that Monty's `ty` checker understands:

- Primitives: `int`, `float`, `str`, `bool`, `None`
- Collections: `list[T]`, `dict[K, V]`, `tuple[T, ...]`, `set[T]`
- Unions: `T | None`, `int | str`
- `Any`
- Nested combinations of the above

As Monty's type checker evolves (e.g., adding class support), grail's stub generation will evolve with it.

### IDE Integration

Because `.pym` files are valid Python and `@external` functions have real signatures, IDEs provide:

- Autocomplete for external function parameters
- Type error highlighting on argument mismatches
- Hover documentation from docstrings
- Go-to-definition (lands on the `@external` declaration)

The `grail` package ships with a `py.typed` marker and type stubs for `external`, `Input`, etc., so IDE type checkers understand the grail imports.

---

## 9. Error Reporting

### Design: Errors Map Back to `.pym` Line Numbers

All errors reference the original `.pym` file, not the generated `monty_code.py`. Grail maintains a source map between the two.

### Error Hierarchy

```
grail.GrailError (base)
├── grail.ParseError          — .pym file has syntax errors
├── grail.CheckError          — @external or Input() declarations are malformed
├── grail.InputError          — missing/invalid input at runtime
├── grail.ExternalError       — missing external function implementation
├── grail.ExecutionError      — Monty runtime error
│   └── grail.LimitError      — resource limit exceeded
└── grail.OutputError         — output validation failed
```

### Error Format

```
grail.ExecutionError: analysis.pym:22 — NameError: name 'undefined_var' is not defined

  20 |     total = sum(item["amount"] for item in items)
  21 |
> 22 |     if total > undefined_var:
  23 |         custom = await get_custom_budget(user_id=uid)

Context: This variable is not defined in the script and is not a declared Input().
```

Key principles:
- Line numbers reference the `.pym` file, not generated code
- A few lines of surrounding context are shown
- Actionable suggestion when possible (e.g., "not a declared Input()" hints at what to do)
- The full Monty traceback is available in `.grail/<name>/run.log`

---

## 10. Pause/Resume (Snapshots)

### Design: Thin Wrapper Over Monty's Native Snapshot

For advanced use cases (long-running workflows, distributed execution), grail exposes Monty's pause/resume mechanism.

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

### Serialization

```python
# Serialize for storage/transfer
data = snapshot.dump()  # -> bytes

# Restore later
snapshot = grail.Snapshot.load(data)
result = snapshot.resume(return_value=some_result)
```

This is a direct pass-through to `pydantic_monty.MontySnapshot.dump()` / `.load()`. No base64 wrappers, no custom serialization — just Monty's native bytes.

---

## 11. GrailModel (Deferred)

### Status: Not Included in v2 Launch

A `GrailModel` base class was considered for providing structured input/output types:

```python
# Hypothetical — NOT part of v2
from grail import GrailModel

class ExpenseReport(GrailModel):
    total_members: int
    over_budget: list[dict[str, float]]
```

This is **deferred** until Monty adds class support (on their roadmap). Today, `GrailModel` would have to compile to a `TypedDict` inside Monty while appearing as a Pydantic model on the host — a leaky abstraction that would confuse developers about what's actually happening.

When Monty supports classes, `GrailModel` becomes viable and valuable. Until then, use plain dicts with type annotations — it's honest about what Monty supports.

### Output Validation (Available Now)

For validating return values on the host side, use the `output_model` parameter:

```python
from pydantic import BaseModel

class Report(BaseModel):
    analyzed: int
    over_budget_count: int
    details: list[dict]

result = await script.run(
    inputs={...},
    externals={...},
    output_model=Report,
)
# result is a validated Report instance
```

This is host-side validation only — it doesn't affect what runs inside Monty.

---

## 12. What's Explicitly Removed

These Grail v1 features are **not carried forward**:

| Removed | Rationale |
|---|---|
| `ResourcePolicy` with inheritance and cycle detection | Replaced by flat dicts and named presets |
| `PolicyResolver` and `compose_guards()` | Unnecessary — one level of limits is enough |
| `GrailFilesystem` with permissions, hooks, callbacks | Replaced by plain dict → `OSAccess`. Dynamic behavior → external functions |
| `FilePermission` enum | Monty's sandbox is the permission system |
| `StructuredLogger` | Not Monty-specific. Use `logging` or your own observability |
| `MetricsCollector` | Not Monty-specific. Instrument at the application level |
| `RetryPolicy` and `execute_with_resilience()` | Not Monty-specific. Use `tenacity` or your own retry logic |
| `@secure` decorator | Replaced by `.pym` files — the entire file is the "secure" boundary |
| `MontyContext` (the 13-parameter god object) | Replaced by `grail.load()` → `GrailScript` |
| `ToolRegistry` | External functions are just a dict — no registry needed |
| `StubGenerator` (as public API) | Still exists internally but is not a user-facing concern |
| Debug payload system | Replaced by `.grail/<name>/run.log` and inspectable artifacts |
| `format_validation_error` / `format_runtime_error` | Errors are formatted by default, no utility functions needed |
| Late-import signature probing | Grail v2 targets a specific Monty version, not "any version" |
| Base64 snapshot serialization | Use Monty's native bytes. Base64 encode in your own code if needed |

### Philosophy

If a feature isn't specific to "running Python safely in Monty," it doesn't belong in Grail. Retry logic, structured logging, and metrics collection are application concerns. A library that provides them is overstepping — developers already have preferred tools for these things.

---

## 13. Package Structure

```
src/grail/
├── __init__.py          # Public API: load, run, Snapshot, presets, errors, external, Input
├── script.py            # GrailScript class — load, parse, run, check, start
├── parser.py            # .pym file parsing — AST extraction of @external, Input()
├── stubs.py             # Type stub generation from parsed declarations
├── checker.py           # Monty compatibility validation (unsupported features detection)
├── codegen.py           # .pym → monty_code.py transformation (strip declarations)
├── errors.py            # Error hierarchy and formatting with source mapping
├── limits.py            # Resource limit parsing and presets (STRICT, DEFAULT, PERMISSIVE)
├── snapshot.py          # Thin wrapper around Monty's pause/resume
├── artifacts.py         # .grail/ directory management (write stubs, check.json, etc.)
├── cli.py               # CLI entry point (grail check, grail run, grail init, etc.)
└── py.typed             # PEP 561 marker for IDE type checking
```

### Public API Surface

```python
# Core
grail.load(path, **options) -> GrailScript
grail.run(code, inputs) -> Any                  # inline escape hatch

# Declarations (for use in .pym files)
grail.external                                   # decorator
grail.Input(name, default=...)                   # input declaration

# Snapshots
grail.Snapshot                                   # pause/resume wrapper
grail.Snapshot.load(data) -> Snapshot

# Limits
grail.STRICT                                     # dict
grail.DEFAULT                                    # dict
grail.PERMISSIVE                                 # dict

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

**Total: ~15 public symbols** (down from 37 in v1).

---

## 14. Migration Path

This is a clean break. There is no automated migration from Grail v1.

### For Grail v1 Users

1. **Convert code strings to `.pym` files** — move your sandboxed code out of Python strings and into `.pym` files with `@external` declarations
2. **Replace `MontyContext` with `grail.load()`** — the 13 constructor parameters become 3 optional kwargs
3. **Replace `ToolRegistry` with a plain dict** — `externals={"name": func}`
4. **Replace `GrailFilesystem` with a plain dict** — `files={"/path": content}`
5. **Replace resource policies with a limits dict** — `limits={"max_memory": "16mb"}`
6. **Remove observability code** — add your own `logging`/metrics at the application level
7. **Remove `@secure` decorators** — use `.pym` files instead
8. **Run `grail check`** — catch Monty compatibility issues immediately

### Version Strategy

Grail v2 will be released as `grail >= 2.0.0` (or a rename, TBD). The `grail >= 0.x` / `1.x` line is frozen — no further development.
