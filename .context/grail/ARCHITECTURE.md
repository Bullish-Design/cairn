# ARCHITECTURE.md: System Design

## Component Diagram

Grail acts as a translation layer between your application code and the Monty Rust binary.

## Key Components

- **`MontyContext` (The Orchestrator):** The primary engine that manages the lifecycle of a Monty instance. It consumes Pydantic models and functions, generates the environment stubs, and handles the `run` / `start` / `resume` cycle.
- **`StubGenerator`:** A utility that inspects Pydantic models and Python function signatures to produce the string-based `type_definitions` required by Monty.
- **`SecureDecorator`:** A syntactic sugar layer that wraps standard Python functions, extracts their source code, and executes them within a `MontyContext` transparently.
- **`ResourceGuard`:** Interfaces with Monty’s memory and execution time tracking to enforce limits defined in Pydantic models.

## Data Flow

1. **Initialization:** Developer defines an `InputModel(BaseModel)` and `Tools`.
2. **Synthesis:** Grail generates a Python stub file representing these models and tools.
3. **Validation:** Input data is validated by the host-side Pydantic model.
4. **Execution:** Data is injected into Monty as `inputs`. Monty executes the code in its isolated environment (<1μs startup).
5. **Extraction:** The result is returned and, if a `ResultModel` is provided, parsed back into a Pydantic object.
