# How to Create a Grail `.pym` Script

Grail is the library Cairn uses to safely execute Python code inside Monty — a minimal, sandboxed Python interpreter written in Rust. This guide explains how to write `.pym` files that Cairn agents run.

---

## Table of Contents

1. [What is a `.pym` File?](#1-what-is-a-pym-file)
2. [File Structure](#2-file-structure)
3. [Declaring Inputs](#3-declaring-inputs)
4. [Declaring External Functions](#4-declaring-external-functions)
5. [Executable Code](#5-executable-code)
6. [Return Value](#6-return-value)
7. [Supported Python Features](#7-supported-python-features)
8. [Unsupported Python Features](#8-unsupported-python-features)
9. [Cairn's External Function API](#9-cairns-external-function-api)
10. [Validating with `grail check`](#10-validating-with-grail-check)
11. [Error Handling](#11-error-handling)
12. [Resource Limits](#12-resource-limits)
13. [Examples](#13-examples)
    - [Simple: Echo Task Description](#simple-echo-task-description)
    - [Simple: List and Log Directory Contents](#simple-list-and-log-directory-contents)
    - [Intermediate: Search and Report](#intermediate-search-and-report)
    - [Intermediate: Read, Transform, Write](#intermediate-read-transform-write)
    - [Complex: Full Agent — Refactor and Submit](#complex-full-agent--refactor-and-submit)

---

## 1. What is a `.pym` File?

A `.pym` (Python for Monty) file is a valid Python file that runs inside the Monty interpreter. Monty is a restricted, sandboxed subset of Python — it provides safety guarantees for executing untrusted or AI-generated code.

Key characteristics:

- `.pym` files are **valid Python** — IDEs provide syntax highlighting, autocomplete, and type checking.
- They run inside **Monty**, not CPython, so only a subset of Python is available.
- They declare their external dependencies (inputs and functions) explicitly at the top of the file, making the interface transparent and checkable before execution.
- In Cairn, the orchestrator loads and runs `.pym` scripts as agents, providing file system access through the declared external functions.

---

## 2. File Structure

A `.pym` file has two clear sections:

```python
from grail import external, Input
from typing import Any

# ─── Declarations Section ────────────────────────────────────────────────────
# Declare inputs and external functions here.
# These are read by grail tooling to generate type stubs and validate the file.
# They execute as no-ops inside Monty — the real values are injected at runtime.

task_description: str = Input("task_description")

@external
async def log(message: str) -> bool:
    """Log a message."""
    ...

# ─── Executable Section ──────────────────────────────────────────────────────
# Everything below is the actual Monty code that runs.

await log(message=f"Received task: {task_description}")

{"done": True}
```

**Rules:**

1. The file must be syntactically valid Python 3.10+.
2. All imports must be `from grail import ...` or `from typing import ...`. No other imports are allowed.
3. `@external` functions must have complete type annotations on all parameters and the return type.
4. `@external` function bodies must be `...` (Ellipsis) — never an implementation.
5. `Input()` calls must have a type annotation on the left-hand side.
6. The final expression in the file is the script's return value.

---

## 3. Declaring Inputs

Inputs are values that the host (Cairn) injects at runtime. Declare them with `Input()`:

```python
from grail import Input

# Required input — must be provided at runtime, no default
task_description: str = Input("task_description")

# Optional input — uses default if not provided
max_results: int = Input("max_results", default=100)
verbose: bool = Input("verbose", default=False)
```

**Supported input types:** `str`, `int`, `float`, `bool`, `list[T]`, `dict[K, V]`, `None`, `Any`, and unions like `str | None`.

In Cairn, every agent script receives exactly one standard input:

| Name               | Type  | Description                              |
|--------------------|-------|------------------------------------------|
| `task_description` | `str` | The task the agent was asked to complete |

---

## 4. Declaring External Functions

External functions are callable capabilities provided by the host at runtime. Declare them with `@external`:

```python
from grail import external
from typing import Any

@external
async def read_file(path: str) -> str:
    """Read the contents of a file."""
    ...

@external
async def write_file(path: str, content: str) -> bool:
    """Write content to a file."""
    ...
```

**Rules for `@external`:**

- The decorator is `@external`, imported from `grail`.
- The function signature must have complete type annotations on every parameter and the return type.
- The body must be `...` (a bare Ellipsis literal — not a string, not a `pass`).
- The function can be `async def` (most Cairn tools are async) or `def`.
- The docstring (optional but recommended) becomes hover documentation in your IDE.

Only declare externals you actually call. Declared-but-unused externals produce a `W002` warning from `grail check`.

---

## 5. Executable Code

After the declarations section, write the executable logic. This runs directly in Monty at the top level — there is no `main()` function to define.

```python
# Call external functions with await
data = await read_file(path="config.json")

# Use f-strings
await log(message=f"Read {len(data)} bytes")

# For loops
results = []
for item in some_list:
    processed = await process_item(item=item)
    results.append(processed)

# List comprehensions
names = [m["name"] for m in members]

# Dict comprehensions
index = {item["id"]: item for item in items}

# Conditionals
if len(results) == 0:
    await log(message="No results found")
else:
    await log(message=f"Found {len(results)} results")

# try/except
try:
    content = await read_file(path="missing.txt")
except Exception as e:
    await log(message=f"File not found: {e}")
    content = ""

# Helper functions (closures are supported)
async def process(item: dict) -> str:
    name = item.get("name", "unknown")
    return f"processed:{name}"

result = await process({"name": "example"})
```

---

## 6. Return Value

The last expression in the file is the script's return value. It can be any value — a dict, a list, a string, a bool, or `None`.

```python
# The final expression is the return value
{
    "status": "ok",
    "results": results,
    "count": len(results),
}
```

For Cairn agents, the return value is typically a dict summarizing what was done. The primary output channel is `submit_result()`, which must be called before the script ends. The return value from the script itself is secondary — it is logged but not used for agent review.

---

## 7. Supported Python Features

Monty supports a practical subset of Python:

| Feature                | Example                                          |
|------------------------|--------------------------------------------------|
| Async/await            | `result = await fetch(url="...")`                |
| For loops              | `for x in items: ...`                            |
| While loops            | `while condition: ...`                           |
| If/elif/else           | `if x > 0: ... elif x < 0: ... else: ...`       |
| Try/except/finally     | `try: ... except ValueError as e: ...`          |
| Functions (closures)   | `async def helper(x: int) -> str: ...`          |
| List comprehensions    | `[x * 2 for x in nums if x > 0]`                |
| Dict comprehensions    | `{k: v for k, v in pairs}`                      |
| Set comprehensions     | `{x.lower() for x in words}`                    |
| Generator expressions  | `sum(x for x in nums)`                          |
| F-strings              | `f"Hello {name}, you have {count} items"`        |
| Basic data types       | `int`, `float`, `str`, `bool`, `None`           |
| Collections            | `list`, `dict`, `tuple`, `set`                  |
| Type annotations       | `x: int = 5`                                    |
| Augmented assignment   | `total += item["amount"]`                       |
| Boolean operators      | `x and y`, `x or y`, `not x`                    |
| Comparison operators   | `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`|
| Slicing                | `items[1:5]`, `items[::-1]`                     |
| Tuple unpacking        | `first, *rest = items`                          |

---

## 8. Unsupported Python Features

These Python features are **not available** in Monty. `grail check` will report errors if you use them:

| Feature              | Error Code | Notes                                        |
|----------------------|-----------|----------------------------------------------|
| Class definitions    | E001      | `class Foo: ...` is not supported            |
| Generators / `yield` | E002      | Use list comprehensions instead              |
| `with` statements    | E003      | Use external functions for resource access   |
| `match` statements   | E004      | Use `if/elif/else` chains instead            |
| Arbitrary imports    | E005      | Only `from grail import ...` and `from typing import ...` |
| `lambda`             | —         | Use a named `def` instead                   |
| Standard library     | E005      | No `os`, `json`, `re`, `pathlib`, etc.      |
| `eval` / `exec`      | —         | Not supported in Monty's sandbox            |

**Common workarounds:**

```python
# Instead of: import json; json.loads(text)
# Use an external function:
@external
async def parse_json(text: str) -> dict[str, Any]:
    """Parse JSON string to dict."""
    ...

data = await parse_json(text=raw_text)

# Instead of: with open(path) as f: content = f.read()
# Use the read_file external:
content = await read_file(path="data.txt")

# Instead of: class Config: pass
# Use a plain dict:
config = {"max_size": 100, "mode": "fast"}
```

---

## 9. Cairn's External Function API

Cairn injects these eight external functions into every agent script. Declare only the ones you use.

### `read_file`

```python
@external
async def read_file(path: str) -> str:
    """Read the text contents of a file."""
    ...
```

- `path`: Relative path (no leading `/`, no `..`).
- Returns: Full file contents as a string.
- Raises if the file does not exist.
- Reads from the agent's workspace first, then falls back to the stable (project) workspace.

### `write_file`

```python
@external
async def write_file(path: str, content: str) -> bool:
    """Write text content to a file."""
    ...
```

- `path`: Relative path. Intermediate directories are created automatically.
- `content`: String content to write (up to 10 MB).
- Returns `True` on success.
- Writes to the agent's isolated workspace — the stable workspace is only modified if the agent is accepted.

### `list_dir`

```python
@external
async def list_dir(path: str = ".") -> list[str]:
    """List file names in a directory."""
    ...
```

- `path`: Relative directory path. Defaults to `"."` (the project root).
- Returns: A list of entry names (not full paths) in the directory.

### `file_exists`

```python
@external
async def file_exists(path: str) -> bool:
    """Check if a file exists."""
    ...
```

- `path`: Relative path.
- Returns `True` if the file exists in either the agent workspace or stable workspace.

### `search_files`

```python
@external
async def search_files(pattern: str) -> list[str]:
    """Find files matching a glob pattern."""
    ...
```

- `pattern`: A glob pattern, e.g. `"**/*.py"`, `"src/**/*.ts"`, `"*.json"`.
- Returns: A list of relative file paths matching the pattern.

### `search_content`

```python
@external
async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]:
    """Search file contents for a regex pattern."""
    ...
```

- `pattern`: A regular expression pattern.
- `path`: Directory to search in. Defaults to `"."`.
- Returns: A list of match objects, each with:
  - `"file"`: Relative file path (str)
  - `"line"`: Line number (int, 1-indexed)
  - `"text"`: The matching line content (str)

### `submit_result`

```python
@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the agent's result for human review."""
    ...
```

- `summary`: Human-readable description of what the agent did.
- `changed_files`: List of relative paths to files that were created or modified.
- Returns `True` on success.
- **Must be called before the script ends.** Without a call to `submit_result`, the orchestrator will not transition the agent to the reviewing state.

### `log`

```python
@external
async def log(message: str) -> bool:
    """Emit a log message."""
    ...
```

- `message`: The message to log.
- Returns `True` always.
- Use for progress updates and debugging. Messages appear in the orchestrator's output.

---

## 10. Validating with `grail check`

Before running a script, validate it:

```bash
# Check all .pym files in the current directory (recursive)
grail check

# Check a specific file
grail check my_agent.pym

# Strict mode — warnings become errors (good for CI)
grail check --strict my_agent.pym

# JSON output for programmatic processing
grail check --format json my_agent.pym
```

**Error codes:**

| Code  | Severity | Meaning                                            |
|-------|----------|----------------------------------------------------|
| E001  | Error    | Class definition (not supported in Monty)         |
| E002  | Error    | Generator / `yield` (not supported)               |
| E003  | Error    | `with` statement (not supported)                  |
| E004  | Error    | `match` statement (not supported)                 |
| E005  | Error    | Forbidden import                                   |
| E006  | Error    | `@external` function missing type annotations     |
| E007  | Error    | `@external` function body is not `...`            |
| E008  | Error    | `Input()` without type annotation                 |
| E1xx  | Error    | Type checker errors from Monty's `ty` checker     |
| W001  | Warning  | Bare dict/list as final expression                |
| W002  | Warning  | Declared `@external` never called                 |
| W003  | Warning  | Declared `Input()` never used                     |
| W004  | Warning  | Script exceeds 200 lines                          |

After running `grail check`, inspect `.grail/<script_name>/` for generated artifacts:

- `stubs.pyi` — generated type stubs for Monty's type checker
- `check.json` — validation results
- `externals.json` — extracted external function signatures
- `inputs.json` — extracted input declarations
- `monty_code.py` — the actual code sent to Monty (declarations stripped)
- `run.log` — stdout/stderr from the last execution

---

## 11. Error Handling

### In the Script

Use `try/except` to handle errors from external functions gracefully:

```python
try:
    content = await read_file(path="config.json")
except Exception as e:
    await log(message=f"Could not read config.json: {e}")
    content = "{}"
```

### Error Types from the Host

When `grail.load()` or `script.run()` is called, these exceptions may be raised:

| Exception              | Trigger                                             |
|------------------------|-----------------------------------------------------|
| `grail.ParseError`     | Syntax errors in the `.pym` file                   |
| `grail.CheckError`     | Malformed `@external` or `Input()` declarations    |
| `grail.InputError`     | Missing required input at runtime                  |
| `grail.ExternalError`  | Missing external function implementation           |
| `grail.ExecutionError` | Runtime error inside Monty                         |
| `grail.LimitError`     | Resource limit exceeded (memory, time, recursion)  |
| `grail.OutputError`    | Output failed `output_model` validation            |

Errors reference the original `.pym` file with line numbers — not the generated `monty_code.py`.

---

## 12. Resource Limits

Monty enforces resource limits to prevent runaway scripts. Cairn's defaults (from `ExecutorSettings`):

| Resource         | Cairn Default |
|------------------|---------------|
| Execution time   | 60 seconds    |
| Memory           | 100 MB        |
| Recursion depth  | 1000 frames   |

If a script exceeds a limit, Cairn raises a `ResourceLimitError` or `TimeoutError` and marks the agent as `ERRORED`.

To avoid hitting limits:

- Avoid deep recursion — use loops instead of recursive helpers.
- Process data incrementally rather than loading everything into memory.
- Keep helper functions shallow and focused.

---

## 13. Examples

---

### Simple: Echo Task Description

The minimal valid Cairn agent script. Declares the standard `task_description` input and `submit_result` external, logs what it received, and submits.

**`echo_task.pym`:**
```python
from grail import external, Input

# ─── Declarations ─────────────────────────────────────────────────────────────

task_description: str = Input("task_description")

@external
async def log(message: str) -> bool:
    """Emit a log message."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the agent's result for review."""
    ...

# ─── Executable ───────────────────────────────────────────────────────────────

await log(message=f"Agent received task: {task_description}")

summary = f"Echo agent completed. Task was: {task_description}"
await submit_result(summary=summary, changed_files=[])

{"status": "ok", "task": task_description}
```

**What this demonstrates:**
- Standard `Input("task_description")` pattern
- Minimal `@external` declarations (`log`, `submit_result`)
- f-string usage
- Calling `submit_result` before the return expression

---

### Simple: List and Log Directory Contents

Explore the project structure by listing a directory, then logging each entry.

**`list_project.pym`:**
```python
from grail import external, Input

# ─── Declarations ─────────────────────────────────────────────────────────────

task_description: str = Input("task_description")

@external
async def list_dir(path: str = ".") -> list[str]:
    """List file names in a directory."""
    ...

@external
async def log(message: str) -> bool:
    """Emit a log message."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the agent's result for review."""
    ...

# ─── Executable ───────────────────────────────────────────────────────────────

await log(message="Listing project root...")

entries = await list_dir(path=".")
await log(message=f"Found {len(entries)} entries in project root")

for entry in entries:
    await log(message=f"  - {entry}")

# Check the src directory if it exists
src_entries = await list_dir(path="src")
py_files = [e for e in src_entries if e.endswith(".py")]
await log(message=f"Found {len(py_files)} Python files in src/")

summary = (
    f"Listed project structure. "
    f"Root has {len(entries)} entries, src/ has {len(py_files)} Python files."
)
await submit_result(summary=summary, changed_files=[])

{
    "root_entries": entries,
    "src_py_files": py_files,
}
```

**What this demonstrates:**
- `list_dir` with a specific path
- For loop iteration over results
- List comprehension with a filter (`if e.endswith(".py")`)
- Multi-line string with parentheses

---

### Intermediate: Search and Report

Search the codebase for a pattern, build a report, and write it to a file.

**`find_todos.pym`:**
```python
from grail import external, Input
from typing import Any

# ─── Declarations ─────────────────────────────────────────────────────────────

task_description: str = Input("task_description")
search_pattern: str = Input("search_pattern", default="TODO|FIXME|HACK")
output_path: str = Input("output_path", default="reports/todos.md")

@external
async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]:
    """Search file contents for a regex pattern."""
    ...

@external
async def write_file(path: str, content: str) -> bool:
    """Write text content to a file."""
    ...

@external
async def log(message: str) -> bool:
    """Emit a log message."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the agent's result for review."""
    ...

# ─── Executable ───────────────────────────────────────────────────────────────

await log(message=f"Searching for pattern: {search_pattern}")

matches = await search_content(pattern=search_pattern, path=".")
await log(message=f"Found {len(matches)} matches")

# Group matches by file
by_file: dict[str, list[dict[str, Any]]] = {}
for match in matches:
    file_path = match["file"]
    if file_path not in by_file:
        by_file[file_path] = []
    by_file[file_path].append(match)

# Build a Markdown report
lines = [
    f"# TODO/FIXME Report",
    f"",
    f"Pattern: `{search_pattern}`",
    f"Total matches: {len(matches)}",
    f"Files affected: {len(by_file)}",
    f"",
]

for file_path in sorted(by_file.keys()):
    file_matches = by_file[file_path]
    lines.append(f"## `{file_path}` ({len(file_matches)} matches)")
    lines.append("")
    for m in file_matches:
        lines.append(f"- Line {m['line']}: `{m['text'].strip()}`")
    lines.append("")

report = "\n".join(lines)

await write_file(path=output_path, content=report)
await log(message=f"Report written to {output_path}")

summary = (
    f"Found {len(matches)} occurrences of '{search_pattern}' "
    f"across {len(by_file)} files. Report written to {output_path}."
)
await submit_result(summary=summary, changed_files=[output_path])

{
    "matches_found": len(matches),
    "files_affected": len(by_file),
    "report_path": output_path,
}
```

**What this demonstrates:**
- Multiple `Input()` declarations including optional ones with defaults
- `search_content` with a regex pattern
- Accessing dict keys on results (`match["file"]`, `match["line"]`, `match["text"]`)
- Building up a dict of lists (grouping by file)
- `sorted()` on dict keys
- Multi-line list construction and `"\n".join()`
- Calling `write_file` to produce output
- Reporting the changed file in `submit_result`

---

### Intermediate: Read, Transform, Write

Read a configuration file, update a value, and write it back. Demonstrates file existence checking and defensive read patterns.

**`update_version.pym`:**
```python
from grail import external, Input

# ─── Declarations ─────────────────────────────────────────────────────────────

task_description: str = Input("task_description")
new_version: str = Input("new_version")
config_path: str = Input("config_path", default="pyproject.toml")

@external
async def read_file(path: str) -> str:
    """Read the text contents of a file."""
    ...

@external
async def write_file(path: str, content: str) -> bool:
    """Write text content to a file."""
    ...

@external
async def file_exists(path: str) -> bool:
    """Check if a file exists."""
    ...

@external
async def log(message: str) -> bool:
    """Emit a log message."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the agent's result for review."""
    ...

# ─── Executable ───────────────────────────────────────────────────────────────

await log(message=f"Updating version to {new_version} in {config_path}")

# Check file exists before reading
exists = await file_exists(path=config_path)
if not exists:
    error_msg = f"Config file not found: {config_path}"
    await log(message=error_msg)
    await submit_result(summary=f"Failed: {error_msg}", changed_files=[])
    {"status": "error", "message": error_msg}

# Read the current content
content = await read_file(path=config_path)
lines = content.splitlines()

updated_lines = []
version_updated = False

for line in lines:
    stripped = line.strip()
    # Match lines like: version = "1.2.3"  or  version = '1.2.3'
    if stripped.startswith("version") and "=" in stripped and not version_updated:
        # Preserve any leading whitespace
        prefix = line[: len(line) - len(line.lstrip())]
        updated_lines.append(f'{prefix}version = "{new_version}"')
        version_updated = True
        await log(message=f"Updated version line: {stripped} -> version = \"{new_version}\"")
    else:
        updated_lines.append(line)

if not version_updated:
    await log(message="Warning: no version field found in file")

new_content = "\n".join(updated_lines)
if content.endswith("\n"):
    new_content = new_content + "\n"

await write_file(path=config_path, content=new_content)
await log(message=f"Wrote updated config to {config_path}")

summary = (
    f"Updated version to {new_version} in {config_path}. "
    f"Version field {'found and updated' if version_updated else 'not found'}."
)
await submit_result(summary=summary, changed_files=[config_path])

{
    "status": "ok",
    "version_updated": version_updated,
    "path": config_path,
    "new_version": new_version,
}
```

**What this demonstrates:**
- `file_exists` guard before reading
- `splitlines()` on file content for line-by-line processing
- String methods: `.strip()`, `.startswith()`, `.lstrip()`, `.endswith()`
- Tracking whether a mutation was applied (`version_updated` flag)
- Preserving trailing newlines
- Early return pattern (submitting an error result when preconditions fail)

---

### Complex: Full Agent — Refactor and Submit

A multi-phase agent that searches for files matching a pattern, reads each one, applies a transformation, writes the results back, and submits a comprehensive summary. This example showcases most available external functions and advanced control flow.

**`refactor_imports.pym`:**
```python
from grail import external, Input
from typing import Any

# ─── Declarations ─────────────────────────────────────────────────────────────

task_description: str = Input("task_description")
# The old import to replace, e.g. "from old_module import"
old_import: str = Input("old_import")
# The new import to replace it with, e.g. "from new_module import"
new_import: str = Input("new_import")
# Glob pattern for files to search, e.g. "**/*.py"
file_pattern: str = Input("file_pattern", default="**/*.py")
# Directory to restrict the search (default: entire project)
search_root: str = Input("search_root", default=".")
# Dry run — log changes without writing them
dry_run: bool = Input("dry_run", default=False)

@external
async def search_files(pattern: str) -> list[str]:
    """Find files matching a glob pattern."""
    ...

@external
async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]:
    """Search file contents for a regex pattern."""
    ...

@external
async def read_file(path: str) -> str:
    """Read the text contents of a file."""
    ...

@external
async def write_file(path: str, content: str) -> bool:
    """Write text content to a file."""
    ...

@external
async def file_exists(path: str) -> bool:
    """Check if a file exists."""
    ...

@external
async def log(message: str) -> bool:
    """Emit a log message."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the agent's result for review."""
    ...

# ─── Helper functions ──────────────────────────────────────────────────────────

async def replace_in_file(path: str, old: str, new: str) -> tuple[bool, int]:
    """
    Read a file, replace all occurrences of `old` with `new`, and write it back.
    Returns (was_changed, replacement_count).
    """
    content = await read_file(path=path)
    count = content.count(old)
    if count == 0:
        return False, 0
    updated = content.replace(old, new)
    if not dry_run:
        await write_file(path=path, content=updated)
    return True, count


async def summarize_matches(matches: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Group search matches by file, returning file -> [line_numbers]."""
    by_file: dict[str, list[int]] = {}
    for m in matches:
        f = m["file"]
        if f not in by_file:
            by_file[f] = []
        by_file[f].append(m["line"])
    return by_file

# ─── Executable ───────────────────────────────────────────────────────────────

await log(message=f"Starting import refactor: '{old_import}' -> '{new_import}'")
await log(message=f"File pattern: {file_pattern}, search root: {search_root}")
if dry_run:
    await log(message="DRY RUN mode — no files will be written")

# Phase 1: Find candidate files using search_content
await log(message="Phase 1: Searching for files containing the old import...")

matches = await search_content(pattern=old_import, path=search_root)
candidates_by_file = await summarize_matches(matches)

await log(message=f"Found {len(matches)} occurrences across {len(candidates_by_file)} files")

if len(candidates_by_file) == 0:
    summary = f"No occurrences of '{old_import}' found. Nothing to do."
    await log(message=summary)
    await submit_result(summary=summary, changed_files=[])
    {"status": "ok", "files_changed": 0, "total_replacements": 0}

# Phase 2: Apply replacements
await log(message="Phase 2: Applying replacements...")

changed_files = []
total_replacements = 0
errors = []

for file_path in sorted(candidates_by_file.keys()):
    line_count = len(candidates_by_file[file_path])
    await log(message=f"  Processing {file_path} ({line_count} matching lines)...")

    try:
        was_changed, count = await replace_in_file(
            path=file_path,
            old=old_import,
            new=new_import,
        )
        if was_changed:
            total_replacements += count
            changed_files.append(file_path)
            mode_label = "[DRY RUN] Would update" if dry_run else "Updated"
            await log(message=f"    {mode_label}: {count} replacement(s)")
        else:
            await log(message=f"    Skipped: no replacements needed")
    except Exception as e:
        error_msg = f"Error processing {file_path}: {e}"
        await log(message=f"    ERROR: {error_msg}")
        errors.append(error_msg)

# Phase 3: Verify changes (skip in dry run)
verified_files = []
if not dry_run and len(changed_files) > 0:
    await log(message="Phase 3: Verifying changes...")

    for file_path in changed_files:
        verification = await search_content(pattern=old_import, path=file_path)
        if len(verification) == 0:
            verified_files.append(file_path)
            await log(message=f"  Verified: {file_path}")
        else:
            remaining = len(verification)
            await log(message=f"  Warning: {file_path} still has {remaining} occurrence(s)")

# Phase 4: Build summary and submit
await log(message="Phase 4: Submitting results...")

status_parts = [
    f"Replaced '{old_import}' with '{new_import}'.",
    f"Files modified: {len(changed_files)}.",
    f"Total replacements: {total_replacements}.",
]
if dry_run:
    status_parts.append("(DRY RUN — no files were written)")
if errors:
    status_parts.append(f"Errors encountered: {len(errors)}.")
    for err in errors:
        status_parts.append(f"  - {err}")

summary = " ".join(status_parts)
files_to_report = changed_files if not dry_run else []

await submit_result(summary=summary, changed_files=files_to_report)
await log(message=f"Done. {summary}")

{
    "status": "ok" if not errors else "partial",
    "dry_run": dry_run,
    "files_changed": len(changed_files),
    "total_replacements": total_replacements,
    "changed_files": changed_files,
    "verified_files": verified_files,
    "errors": errors,
}
```

**What this demonstrates:**
- Six inputs, including booleans and strings with defaults
- All eight Cairn external functions declared and used
- Helper functions (closures) defined between declarations and executable code
- A helper that returns a `tuple[bool, int]`
- Multi-phase agent workflow (search → replace → verify → submit)
- `try/except` error collection without aborting the whole script
- Early exit pattern using a final expression before the main loop
- Conditional writes (dry run mode)
- Building a human-readable multi-sentence summary
- Rich return dict with status, metrics, and file lists

---

## Appendix: Quick Reference

### Minimal Cairn Agent Template

```python
from grail import external, Input

task_description: str = Input("task_description")

@external
async def log(message: str) -> bool:
    """Emit a log message."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the agent's result for review."""
    ...

# Your logic here
await log(message=f"Starting: {task_description}")

# ... do work ...

await submit_result(summary="Task complete.", changed_files=[])
{"status": "ok"}
```

### All Cairn External Function Signatures

```python
from grail import external
from typing import Any

@external
async def read_file(path: str) -> str: ...

@external
async def write_file(path: str, content: str) -> bool: ...

@external
async def list_dir(path: str = ".") -> list[str]: ...

@external
async def file_exists(path: str) -> bool: ...

@external
async def search_files(pattern: str) -> list[str]: ...

@external
async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]: ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool: ...

@external
async def log(message: str) -> bool: ...
```

### `grail check` Cheat Sheet

```bash
grail check                        # Check all .pym files
grail check my_agent.pym           # Check one file
grail check --strict my_agent.pym  # Warnings as errors
grail check --format json          # JSON output for CI
grail watch                        # Auto-check on file changes
grail clean                        # Remove .grail/ artifacts
```
