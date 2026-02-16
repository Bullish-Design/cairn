"""Input declaration for .pym files."""

from typing import Any, TypeVar, overload

T = TypeVar("T")


@overload
def Input(name: str) -> Any: ...


@overload
def Input(name: str, default: T) -> T: ...


def Input(name: str, default: Any = None) -> Any:
    """
    Declare an input variable that will be provided at runtime.

    This is a no-op at runtime - it exists for grail's parser to extract
    input declarations. At Monty runtime, these become actual variable bindings.

    Usage:
        budget_limit: float = Input("budget_limit")
        department: str = Input("department", default="Engineering")

    Requirements:
    - Must have a type annotation

    Args:
        name: The input variable name
        default: Optional default value if not provided at runtime

    Returns:
        The default value if provided, otherwise None (at parse time)
    """
    return default
