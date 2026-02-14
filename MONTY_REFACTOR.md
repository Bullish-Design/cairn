# Monty Extraction: Exploration & Options

Status: Exploration
Date: 2026-02-14

## Context

Cairn currently uses `pydantic-monty` as an embedded execution sandbox for AI-generated agent code. The integration spans four files (`executor.py`, `external_functions.py`, `external_models.py`, `code_generator.py`) and one settings class (`ExecutorSettings`), totaling ~450 lines of Monty-adjacent code interleaved with Cairn-specific concerns (AgentFS overlays, lifecycle state, submission records).

The goal of this document is to explore extracting a **standalone Monty wrapper library** that:

1. Makes Monty easier for any developer to use in their projects.
2. Simplifies Cairn's codebase by pushing execution concerns out of the orchestrator.
3. Enables new use cases: calling `ty` typechecker on outputs, reacting to LSP events, running scripts on treesitter nodes.

---

## Current Integration Map

Where Monty touches Cairn today:

| File | Monty Responsibility | Cairn Responsibility |
|------|---------------------|----------------------|
| `executor.py` | Create `Monty` instance, call `run_monty_async`, handle `MontySyntaxError`/`MontyRuntimeError`, configure `ResourceLimits` | `ExecutionResult` dataclass, `agent_id` tracking, duration timing |
| `external_functions.py` | Pass dict of callables to Monty | AgentFS overlay fallthrough, KV submission storage, LLM delegation |
| `external_models.py` | (none directly) | Pydantic validation of function arguments/responses |
| `code_generator.py` | Hardcoded Monty constraints in prompt template, `validate_code()` forbidden-pattern checks | LLM model selection, prompt formatting |
| `settings.py` | `ExecutorSettings` (time, memory, recursion limits) | `OrchestratorSettings`, `PathsSettings` |
| `orchestrator.py` | `self.executor.execute(...)` call site, `self.executor.validate_code(...)` | Everything else: lifecycle, queue, FS, signals, materialization |

### Observations

1. **Validation is split across two files.** `executor.py` has `validate_code()` (syntax-only via `compile()`). `code_generator.py` has its own `validate_code()` (syntax + forbidden patterns + `submit_result` check). These serve different purposes but the naming overlap is confusing.

2. **Monty features Cairn doesn't use yet.** Type checking (`type_check=True`, `type_check_stubs`), iterative execution (`start()`/`resume()`), serialization/snapshotting (`dump()`/`load()`). These are exactly the features needed for the ty/LSP/treesitter use cases.

3. **External functions are Cairn-specific but the pattern is generic.** The idea of "declare functions → pass them to Monty → get results" is reusable. The specific functions (read_file, write_file, etc.) and their AgentFS backing are Cairn's business.

4. **Error categorization is baked into Cairn.** The executor does string-matching on error messages (`"timeout" in error_msg.lower()`) to classify errors. This is a generic concern any Monty user would want.

5. **The code generator prompt hardcodes Monty constraints.** The list of available functions and constraints ("no imports, no classes, no open()") is written as a string literal. If the wrapper library knew the function signatures, it could generate this section automatically.

---

## Option A: Thin Execution Wrapper ("monty-run")

A minimal library that wraps the execute-and-handle-errors cycle without opinions about what external functions look like.

### What It Owns

- `MontyRunner`: configure resource limits, execute code, return structured results.
- `RunResult`: success/failure with typed error categories (syntax, timeout, memory, recursion, runtime, unknown).
- `RunConfig`: resource limits as a validated settings object.
- Error classification logic (no more string-matching in downstream code).
- Optional: code validation helpers (syntax check, forbidden-pattern scan).

### API Sketch

```python
from monty_run import MontyRunner, RunConfig

runner = MontyRunner(
    config=RunConfig(max_duration_secs=30, max_memory=50_000_000),
)

result = runner.execute(
    code="data = fetch('https://example.com')\nlen(data)",
    external_functions={"fetch": my_fetch_fn},
    script_name="agent-abc.py",
)

if result.failed:
    print(result.error_type)  # "timeout" | "memory" | "syntax" | ...
    print(result.error)
```

### Cairn Impact

| Aspect | Before | After |
|--------|--------|-------|
| `executor.py` | 192 lines, owns Monty creation + error handling + timing | ~30 lines, delegates to `MontyRunner`, wraps `RunResult` → `ExecutionResult` with `agent_id` |
| `settings.py` | `ExecutorSettings` with Monty-specific limits | Remove `ExecutorSettings`, import `RunConfig` from `monty-run` |
| Error handling | String-matching in `executor.py` | Handled upstream, Cairn gets typed `error_type` |
| Code validation | Split across `executor.py` and `code_generator.py` | `monty-run` provides `validate()`, Cairn adds its own domain checks on top |

### Pros

- Minimal scope, fast to build and stabilize.
- Cairn's executor shrinks to a thin adapter.
- Other projects get structured error handling for free.
- No opinions about external functions—users bring their own.

### Cons

- Doesn't address the external function boilerplate pattern.
- Doesn't help with ty/LSP/treesitter use cases—those need iterative execution and type stubs.
- Cairn still owns all the complexity of function declaration, prompt generation, and type stub management.

---

## Option B: Function-Aware Execution Framework ("monty-kit")

A library that understands external functions as first-class declarations and can generate type stubs, prompt fragments, and validation rules from those declarations.

### What It Owns

Everything from Option A, plus:

- `@monty_function` decorator or `FunctionSpec` for declaring external functions with types and docstrings.
- Automatic generation of `type_check_stubs` from function specs (for `ty` integration).
- Automatic generation of prompt constraint text ("Available functions: ...") from function specs.
- Code validation that knows which functions are declared (catches calls to undeclared functions).
- Built-in support for `type_check=True` in execution.

### API Sketch

```python
from monty_kit import MontyKit, RunConfig, monty_function

@monty_function
async def read_file(path: str) -> str:
    """Read file contents."""
    ...

@monty_function
async def write_file(path: str, content: str) -> bool:
    """Write content to file."""
    ...

kit = MontyKit(
    functions=[read_file, write_file],
    config=RunConfig(max_duration_secs=30),
    type_check=True,  # run ty before execution
)

# For LLM prompt generation
prompt_fragment = kit.prompt_fragment()
# "Available functions:\n- read_file(path: str) -> str\n- write_file(path: str, content: str) -> bool\n..."

# For validation
is_valid, errors = kit.validate(code)

# For execution
result = await kit.execute(code)

# For ty-only checking (no execution)
type_errors = kit.type_check(code)
```

### Cairn Impact

| Aspect | Before | After |
|--------|--------|-------|
| `executor.py` | 192 lines | Remove entirely. `MontyKit.execute()` replaces it. |
| `external_functions.py` | 326 lines defining 9 functions + factory | Keep `CairnExternalFunctions` class but decorate methods with `@monty_function`. Remove `create_external_functions` factory—`MontyKit` builds the dict. |
| `external_models.py` | 156 lines of request/response Pydantic models | Keep for Cairn's internal validation. `monty-kit` generates type stubs from the function signatures independently. |
| `code_generator.py` | Hardcoded prompt template with function list | Call `kit.prompt_fragment()` instead of maintaining the list manually. Constraints section generated automatically. |
| `settings.py` | `ExecutorSettings` | Remove, use `RunConfig` from `monty-kit`. |
| `orchestrator.py` | Creates executor + calls validate + execute separately | Single `kit.execute(code)` call. |
| **New: ty integration** | Not possible | `kit.type_check(code)` before execution, or `type_check=True` for automatic pre-execution checking. |

### Pros

- Cairn loses ~350 lines of code across executor, settings, and code generator.
- Function declarations become the single source of truth for: Monty function names, type stubs for ty, prompt fragments for LLM, validation rules.
- Type checking with ty becomes trivial—the stubs are auto-generated.
- Other developers get a complete "declare functions → run code" pipeline.
- Prompt generation stays in sync with actual function signatures automatically.

### Cons

- Larger library scope, more API surface to maintain.
- The `@monty_function` decorator adds a layer of abstraction that may be surprising.
- Cairn's external function validation (Pydantic models in `external_models.py`) is more rigorous than what function signatures alone can express. Need to decide whether monty-kit validates or Cairn validates.

---

## Option C: Reactive Script Engine ("monty-engine")

A library that extends Option B with support for iterative execution, event-driven script dispatch, and persistent state—targeting the ty/LSP/treesitter pipeline directly.

### What It Owns

Everything from Option B, plus:

- **Iterative execution manager**: wraps `start()`/`resume()` cycle with middleware hooks.
- **Script registry**: named scripts that can be triggered by events (type errors, LSP diagnostics, treesitter node matches).
- **Event dispatch**: `engine.on("ty:error", handler_script)`, `engine.on("lsp:diagnostic", handler_script)`.
- **State serialization**: manage `dump()`/`load()` for suspending/resuming script execution across process boundaries.
- **Pipeline composition**: chain scripts together (e.g., "run ty → if errors → run fix script → re-run ty").

### API Sketch

```python
from monty_engine import MontyEngine, RunConfig, Script, monty_function

@monty_function
async def read_file(path: str) -> str: ...

@monty_function
async def write_file(path: str, content: str) -> bool: ...

@monty_function
async def get_diagnostics(path: str) -> list[dict]: ...

engine = MontyEngine(
    functions=[read_file, write_file, get_diagnostics],
    config=RunConfig(max_duration_secs=30),
)

# Register scripts that react to events
engine.register("fix_type_errors", Script(
    code="errors = get_diagnostics(path)\nfor e in errors:\n  ...",
    trigger="ty:error",
))

# Run ty on a file and dispatch to registered handlers
results = await engine.check_and_dispatch("src/main.py")

# Or run a pipeline explicitly
pipeline_result = await engine.pipeline([
    ("ty:check", {"path": "src/main.py"}),
    ("fix_type_errors", {"path": "src/main.py"}),
    ("ty:check", {"path": "src/main.py"}),  # verify fix
])

# Suspend mid-execution (e.g., waiting for user input)
snapshot = engine.suspend()
stored_bytes = snapshot.dump()

# Resume later, possibly in a different process
engine2 = MontyEngine.resume(stored_bytes)
result = await engine2.continue_execution()
```

### Cairn Impact

| Aspect | Before | After |
|--------|--------|-------|
| `executor.py` | 192 lines | Remove entirely. |
| `external_functions.py` | 326 lines | Decorated function definitions only (~100 lines). |
| `code_generator.py` | 135 lines with hardcoded template | ~50 lines. Prompt fragment from engine. Validation from engine. |
| `orchestrator.py` | Owns execute step inline in `_run_agent` | Delegates to `engine.execute()` or `engine.pipeline()`. |
| **New: ty pipeline** | Not possible | Register type-check → fix → re-check pipeline with engine. |
| **New: LSP reaction** | Not possible | Register scripts triggered by LSP diagnostic events. |
| **New: treesitter** | Not possible | Register scripts triggered on specific AST node patterns (via external function that queries treesitter). |
| **New: agent suspend/resume** | Not possible | Snapshot agent execution mid-flight, persist to AgentFS KV, resume later. |

### Pros

- Directly enables the ty/LSP/treesitter use cases described in the goal.
- Pipeline composition makes complex workflows declarative.
- Suspend/resume with serialization maps perfectly to Cairn's model (agents can be paused, persisted, and resumed).
- Cairn's orchestrator becomes much simpler—it manages agent lifecycle, the engine manages execution complexity.
- Other developers get a full reactive scripting framework.

### Cons

- Significantly larger scope. More concepts to design and maintain.
- The event/dispatch model adds a new abstraction layer that may not be needed for simpler use cases.
- Risk of over-engineering if the ty/LSP workflows turn out to be simpler than expected.
- The Script registry is a new concept that overlaps with Cairn's task queue.

---

## Feature-Level Analysis

### Feature 1: ty Type Checking Integration

**What it enables**: Run Astral's ty type checker on generated code before execution, using auto-generated stubs for external functions.

| Option | How It Works | Effort | Cairn Benefit |
|--------|-------------|--------|---------------|
| A (thin wrapper) | Cairn must manually construct `type_check_stubs` string and pass `type_check=True` to `pydantic_monty.Monty()` | Cairn owns all stub generation | Low—Cairn still does the work |
| B (function-aware) | `kit.type_check(code)` generates stubs from `@monty_function` signatures automatically | Library owns stub generation | High—Cairn calls one method |
| C (engine) | Same as B, plus can chain ty results into fix scripts | Library owns everything | Highest—full pipeline |

**Recommendation**: This feature alone justifies Option B over A. Stub generation from function declarations is mechanical work that every Monty user would need to duplicate.

### Feature 2: LSP Event Reactions

**What it enables**: When an LSP server reports a diagnostic (error, warning) for a file, trigger a Monty script to handle it.

| Option | How It Works | Cairn Benefit |
|--------|-------------|---------------|
| A | Cairn builds the event→script dispatch itself | None—all Cairn code |
| B | Cairn builds the dispatch but uses `kit.execute()` for each script | Execution is simpler |
| C | `engine.on("lsp:diagnostic", script)` handles dispatch | Cairn just registers scripts |

**Recommendation**: This feature is about dispatch, not execution. Option C's event system is helpful but could also be a separate concern layered on top of B. The core question is whether Cairn or the library owns the concept of "named scripts triggered by events."

### Feature 3: Treesitter Node Scripts

**What it enables**: Run Monty scripts that receive treesitter AST nodes as input and produce edits, diagnostics, or metadata.

| Option | How It Works | Cairn Benefit |
|--------|-------------|---------------|
| A | Cairn exposes treesitter data as an external function, builds dispatch | None from library |
| B | Same, but with typed function declaration and stubs | Type-safe treesitter queries |
| C | Engine integrates treesitter query → script dispatch | Declarative node → script mapping |

**Recommendation**: Treesitter integration is inherently a "provide data via external function" pattern. The library doesn't need to know about treesitter. What matters is that external functions are easy to declare (Option B) and that scripts can be triggered reactively (Option C).

### Feature 4: Script Suspend/Resume

**What it enables**: Pause Monty execution at an external function call, serialize state, persist to storage, and resume later (possibly in a different process).

| Option | How It Works | Cairn Benefit |
|--------|-------------|---------------|
| A | Cairn wraps `start()`/`resume()` and `dump()`/`load()` itself | None from library |
| B | Library could add `kit.start()` / `kit.resume()` but it's thin | Marginal |
| C | Engine manages the full suspend/resume lifecycle | Significant—maps to agent persistence |

**Recommendation**: This is a natural fit for Cairn's agent model. Agents already have lifecycle state and persistence via AgentFS KV. Engine-managed suspend/resume would let agents pause mid-execution (e.g., waiting for human approval of a dangerous operation) and resume later.

### Feature 5: Pipeline Composition

**What it enables**: Define multi-step workflows ("check types → fix errors → re-check → submit") as declarative pipelines.

| Option | How It Works | Cairn Benefit |
|--------|-------------|---------------|
| A | Cairn builds pipelines in `_run_agent()` | All Cairn code |
| B | Cairn calls `kit.execute()` multiple times in sequence | Execution is cleaner |
| C | `engine.pipeline([step1, step2, ...])` | Declarative, reusable |

**Recommendation**: Pipelines are powerful but add significant API surface. Consider whether this is better as a Cairn-level concept (since Cairn already has agent lifecycle) or a library concept. If pipelines are generic enough that non-Cairn users want them, they belong in the library.

---

## Recommendation

**Start with Option B ("monty-kit"), design the API to allow Option C later.**

Rationale:

1. **Option A is too thin.** It saves Cairn ~100 lines but doesn't address the core pain: function declarations are the source of truth for stubs, prompts, and validation, and that logic needs to live somewhere reusable.

2. **Option C is too ambitious for an initial extraction.** The event dispatch and pipeline systems are valuable but should be designed after real usage patterns emerge from Cairn's ty/LSP work. Building them speculatively risks wrong abstractions.

3. **Option B hits the sweet spot.** It extracts the right concepts (function-aware execution, ty integration, prompt generation) without introducing event systems or pipeline DSLs. It directly enables the ty use case and makes LSP/treesitter work easier.

4. **Option B's API naturally extends to C.** A `MontyKit` that knows about functions and can execute/type-check code is a natural foundation for adding `on()` event handlers and `pipeline()` later.

### Migration Path

```
Phase 1: Extract monty-kit with MontyKit + @monty_function + RunConfig
         Cairn: replace executor.py, simplify code_generator.py

Phase 2: Add ty integration to monty-kit (type_check_stubs generation)
         Cairn: enable pre-execution type checking

Phase 3: Add iterative execution to monty-kit (start/resume wrappers)
         Cairn: implement agent suspend/resume via AgentFS KV

Phase 4: Consider event dispatch (Option C) based on real usage patterns
         from ty/LSP/treesitter work in Cairn
```

---

## What Cairn Looks Like After Option B

### Files Removed or Gutted

- `executor.py`: **removed** (replaced by `monty-kit`)
- `settings.py`: `ExecutorSettings` **removed** (replaced by `RunConfig` from `monty-kit`)

### Files Simplified

- `code_generator.py`: prompt template uses `kit.prompt_fragment()` instead of hardcoded function list. `validate_code()` delegates to `kit.validate()` + Cairn-specific checks (e.g., `submit_result` required).
- `orchestrator.py`: `_run_agent()` calls `kit.execute()` directly. No more `self.executor` indirection.
- `external_functions.py`: functions decorated with `@monty_function`. `create_external_functions` factory replaced by `MontyKit` construction.

### Files Unchanged

- `external_models.py`: still validates inputs/outputs with Pydantic (this is Cairn's domain validation, not Monty's concern).
- `agent.py`, `queue.py`, `lifecycle.py`, `workspace.py`, etc.: no Monty involvement.

### Conceptual Simplification

Before: Cairn has 6 concepts that touch Monty.
- `AgentExecutor` (Monty wrapper)
- `ExecutorSettings` (Monty config)
- `ExternalFunctions` protocol + `CairnExternalFunctions` class + `create_external_functions` factory (Monty function plumbing)
- `CodeGenerator.validate_code()` + `AgentExecutor.validate_code()` (two validators)
- `CodeGenerator.PROMPT_TEMPLATE` (hardcoded function list)
- `ExecutionResult` (Monty result wrapper)

After: Cairn has 2 concepts that touch Monty.
- `MontyKit` instance (configured once with functions and limits)
- `CairnExternalFunctions` (decorated methods that implement Cairn's host API)

Everything else—execution, error classification, validation, stub generation, prompt fragments—is the kit's problem.

---

## Open Questions

1. **Naming.** `monty-kit`, `monty-run`, `monty-engine`, or something else? Should it be `pydantic-monty-kit` to signal the relationship to `pydantic-monty`? Or completely independent?

2. **Pydantic dependency.** `monty-kit` could use Pydantic for its own config/validation (natural since `pydantic-monty` is from Pydantic), or stay dependency-light. Cairn already uses Pydantic, so no cost there—but other users might prefer a lighter dependency.

3. **Async-only vs sync+async.** Cairn is async-only. Monty supports both. Should the wrapper support both, or only async?

4. **Validation ownership.** Cairn's `external_models.py` does deep validation (path traversal checks, file size limits). Should `monty-kit` validate function inputs at all, or leave that entirely to the function implementations? Currently leaning toward: the library validates types match the declared signatures; domain validation (path safety, size limits) stays in the function implementations.

5. **Where does `CodeGenerator` live?** It uses an LLM to generate code. If `monty-kit` provides prompt fragments, the generator itself should stay in Cairn (it's Cairn-specific how code is generated). But the prompt fragment format and validation rules could be shared.
