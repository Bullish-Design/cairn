"""Tool registration helpers for deterministic external function access."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


class ToolRegistry:
    """Maintain a deterministic mapping of callable names to tools."""

    def __init__(self, tools: list[Callable[..., Any]] | None = None) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def as_mapping(self) -> dict[str, Callable[..., Any]]:
        return {name: self._tools[name] for name in self.names}

    def register(self, tool: Callable[..., Any], *, name: str | None = None) -> None:
        tool_name = name or tool.__name__
        if tool_name in self._tools:
            raise ValueError(f"Duplicate tool registration for '{tool_name}'")
        self._tools[tool_name] = tool

    async def invoke(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool '{name}'. Available tools: {', '.join(self.names)}")

        result = self._tools[name](*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
