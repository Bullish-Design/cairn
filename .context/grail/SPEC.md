# SPEC.md: Technical Specification

## 1. The `MontyContext` API

The core class for managing isolated environments.

```python
class MontyContext(Generic[InputT, OutputT]):
    def __init__(
        self,
        input_model: type[InputT],
        tools: list[Callable] = None,
        output_model: type[OutputT] = None,
        limits: ResourceLimits = None,  # Wrapper for Monty's resource tracking
    ): ...

    def execute(self, code: str, inputs: InputT) -> OutputT:
        """
        1. Validates 'inputs' against input_model.
        2. Generates stubs for 'tools' and 'input_model'.
        3. Calls grail.run_monty_async under the hood.
        4. Parses result into output_model.
        """
```

## 2. The `@secure` Decorator

Provides the “Option 2” functionality by using `MontyContext` internally.

```python
@secure(
    limits=ResourceLimits(memory_mb=10, timeout_ms=100),
    allowed_tools=[my_tool]
)
def isolated_logic(data: MyModel) -> str:
    # Source code is extracted and run via Monty
    return f"Processed {data.name}"
```

## 3. Resource Limits

Mapped directly to Monty’s internal tracking.

| Pydantic Field | Monty Implementation | Description |
| --- | --- | --- |
| `max_memory` | `track memory usage` | Max allocations allowed. |
| `max_ticks` | `execution time` | Limits infinite loops. |
| `allow_async` | `asyncio` support | Toggles async execution. |

## 4. Persistence & Snapshots

Grail will expose Monty’s `dump()` and `load()` methods as Pydantic-compatible byte-strings or Base64 strings, making it trivial to save an agent’s state to a database.
