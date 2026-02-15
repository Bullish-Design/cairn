"""Developer ergonomics helpers such as ``@secure``."""

from __future__ import annotations

import inspect
import textwrap
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast, get_type_hints

from pydantic import BaseModel, create_model

from .context import MontyContext
from .types import ResourceLimits

P = ParamSpec("P")
R = TypeVar("R")


def secure(
    *,
    limits: ResourceLimits | None = None,
    tools: list[Callable[..., Any]] | None = None,
    debug: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Execute a typed function inside Monty with inferred models.

    Example:
        >>> from pydantic import BaseModel
        >>> class Out(BaseModel):
        ...     value: int
        >>> @secure()
        ... def add(a: int, b: int) -> int:
        ...     return a + b
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        hints = get_type_hints(func)
        return_type = hints.get("return", Any)
        signature = inspect.signature(func)

        fields: dict[str, tuple[Any, Any]] = {}
        for name, param in signature.parameters.items():
            annotation = hints.get(name, Any)
            default = ... if param.default is inspect.Signature.empty else param.default
            fields[name] = (annotation, default)

        input_model = create_model(f"{func.__name__.title()}Input", **fields)

        output_is_model = isinstance(return_type, type) and issubclass(return_type, BaseModel)
        if output_is_model:
            output_model = cast(type[BaseModel], return_type)
        else:
            output_model = create_model(f"{func.__name__.title()}Output", result=(return_type, ...))

        source = inspect.getsource(func)
        raw_lines = textwrap.dedent(source).splitlines()
        body_lines = [line for line in raw_lines if not line.strip().startswith("@")]
        code = "\n".join(body_lines) + f"\n{func.__name__}(**inputs)"

        ctx = MontyContext(
            input_model, limits=limits, output_model=output_model, tools=tools, debug=debug
        )

        @wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = signature.bind(*args, **kwargs)
            result = ctx.execute(code, dict(bound.arguments))
            if output_is_model:
                return cast(R, result)
            return cast(R, getattr(result, "result"))

        return wrapped

    return decorator
