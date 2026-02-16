"""Type stubs for grail declarations."""

from typing import Any, Callable, TypeVar, overload

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T")

def external(func: F) -> F:
    """Mark a function as externally provided."""
    ...

@overload
def Input(name: str) -> Any: ...
@overload
def Input(name: str, default: T) -> T: ...
def Input(name: str, default: Any = None) -> Any:
    """Declare an input variable."""
    ...
