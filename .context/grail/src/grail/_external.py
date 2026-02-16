"""External function decorator for .pym files."""

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def external(func: F) -> F:
    """
    Decorator to mark a function as externally provided.

    This is a no-op at runtime - it exists purely for grail's parser
    to extract function signatures and generate type stubs.

    Usage:
        @external
        async def fetch_data(url: str) -> dict[str, Any]:
            '''Fetch data from URL.'''
            ...

    Requirements:
    - Function must have complete type annotations
    - Function body must be ... (Ellipsis)
    """
    setattr(func, "__grail_external__", True)
    return func
