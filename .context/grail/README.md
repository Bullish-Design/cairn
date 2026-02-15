# Grail

## Quick start

Install:

```bash
pip install grail
```

Run typed sandboxed code in under a minute:

```python
from pydantic import BaseModel
from grail import MontyContext

class InputModel(BaseModel):
    a: int
    b: int

class OutputModel(BaseModel):
    total: int

# Tool exposed to the sandbox

def add(a: int, b: int) -> int:
    return a + b

ctx = MontyContext(
    InputModel,
    output_model=OutputModel,
    tools=[add],
    policy="strict",  # built-in resource policy preset
)

result = ctx.execute(
    "{'total': add(inputs['a'], inputs['b'])}",
    {"a": 20, "b": 22},
)

print(result.total)  # 42
```

---

Grail is a **Pydantic-native wrapper around Monty** for running untrusted/AI-generated Python code with strong boundaries, typed contracts, and production-friendly observability.

## What Grail provides

- **Typed input/output contracts** using Pydantic models.
- **Sandbox orchestration** through a single `MontyContext` API.
- **Tool allowlisting** via explicit Python callables.
- **Automatic type-stub generation** for model/tool signatures.
- **Resource governance** with limits, guards, and named policies.
- **Filesystem control** with explicit read/write/deny permissions.
- **Resumable execution** using snapshot dump/load helpers.
- **Debug + observability hooks** for events, metrics, and structured logs.

## How it works

1. You define `input_model` / `output_model` (Pydantic) and optional tools.
2. Grail validates host-side input before execution.
3. Grail generates deterministic stubs from your models and tool signatures.
4. Grail runs code in Monty and maps runtime failures to Grail error types.
5. Grail validates output against your output model (if configured).

## Installation & requirements

- Python **3.13+**
- Dependencies:
  - `pydantic>=2.12.5`
  - `pydantic_monty`

Install from your package index:

```bash
pip install grail
```

For local development:

```bash
pip install -e .[dev]
```

## Core API

Primary exports are available from `grail`:

- `MontyContext`
- `secure`
- `ResourceGuard`, `ResourceLimits`
- `STRICT_POLICY`, `PERMISSIVE_POLICY`, `AI_AGENT_POLICY`
- `GrailFilesystem`, `memory_filesystem`, `hooked_filesystem`, `callback_filesystem`
- `MontySnapshot` and snapshot base64/serialization helpers
- `ToolRegistry`
- `StructuredLogger`, `MetricsCollector`, `RetryPolicy`

## Common usage patterns

### 1) Context-based execution

Use when you want explicit control over models, tools, limits, and lifecycle:

```python
from pydantic import BaseModel
from grail import MontyContext

class InputModel(BaseModel):
    text: str

ctx = MontyContext(InputModel)
result = ctx.execute("inputs['text'].upper()", {"text": "hello"})
print(result)  # HELLO
```

### 2) Decorator ergonomics with `@secure`

Use when you want to sandbox a typed function with minimal boilerplate:

```python
from grail import secure

@secure()
def multiply(a: int, b: int) -> int:
    return a * b

assert multiply(6, 7) == 42
```

`@secure` infers input/output models from function annotations and executes function source inside Monty.

### 3) Policy/limit composition

Limits are resolved with precedence:

`defaults < policy < guard < explicit limits`

```python
from grail import MontyContext, ResourceGuard

ctx = MontyContext(
    input_model=InputModel,
    policy="strict",
    guard=ResourceGuard(max_duration_secs=0.75),
    limits={"max_duration_secs": 2.0},
)
```

### 4) Filesystem boundaries

Default strategy is deny-by-default. Explicitly grant what code should access.

```python
from grail import FilePermission, memory_filesystem

fs = memory_filesystem(
    files={"/data/in.txt": "hello"},
    permissions={"/data": FilePermission.READ, "/tmp": FilePermission.WRITE},
)

ctx = MontyContext(InputModel, filesystem=fs)
```

### 5) Resumable execution + snapshots

For pause/resume workflows (for example, tool approval checkpoints):

```python
snapshot = await ctx.start(code, inputs)

if not snapshot.is_complete:
    payload = snapshot.dump()  # persist this payload

# later
restored = ctx.load_snapshot(payload)
next_snapshot = restored.resume(...)
```

## Errors and failure modes

Grail provides focused exceptions:

- `GrailValidationError`: input validation errors
- `GrailOutputValidationError`: output failed output-model validation
- `GrailExecutionError`: runtime failure in Monty execution
- `GrailLimitError`: likely resource-limit violation

## Observability and resilience

- `StructuredLogger` receives lifecycle and retry/fallback events.
- `MetricsCollector` tracks counters and timing summaries.
- `MontyContext.execute_with_resilience(...)` adds retries and optional fallback behavior.
- Debug mode (`debug=True`) captures events, stdout/stderr, tool-call records, and resource metrics in `ctx.debug_payload`.

## Security model (high-level)

Grail is designed for hostile/untrusted code scenarios:

- Monty provides isolation boundaries.
- Tools are explicitly allowlisted.
- Filesystem access is explicitly permissioned.
- Inputs/outputs are validated with Pydantic contracts.
- Threat model documentation: `security/THREAT_MODEL.md`

## Project layout

- `src/grail/`: core library
- `examples/basic/`: minimal runnable example
- `docs/`: getting started, concepts, API reference, migration guide
- `tests/`: unit, integration, and contract tests
- `benchmarks/`: benchmark harness and latest results

## Additional docs

- `docs/getting-started.md`
- `docs/concepts.md`
- `docs/api-reference.md`
- `docs/migration-guide.md`
- `ARCHITECTURE.md`
- `CONCEPT.md`

## Status

Early version (`0.1.0`) with active iteration; API may evolve. Prefer pinned versions in production.
