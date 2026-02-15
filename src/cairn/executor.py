"""Agent code executor using Grail's MontyContext."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel

from grail import GrailExecutionError, GrailLimitError, MontyContext

from cairn.settings import ExecutorSettings


class EmptyInput(BaseModel):
    """No-input model for Cairn generated agent scripts."""


@dataclass
class ExecutionResult:
    """Result of agent code execution."""

    success: bool
    return_value: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    duration_ms: float = 0.0
    agent_id: str = ""

    @property
    def failed(self) -> bool:
        """Whether execution failed."""
        return not self.success


class AgentExecutor:
    """Executes agent code in a Grail sandbox with resource limits."""

    def __init__(
        self,
        max_execution_time: float | None = None,
        max_memory_bytes: int | None = None,
        max_recursion_depth: int | None = None,
        settings: ExecutorSettings | None = None,
    ):
        resolved = settings or ExecutorSettings()
        effective = ExecutorSettings(
            max_execution_time=(
                max_execution_time if max_execution_time is not None else resolved.max_execution_time
            ),
            max_memory_bytes=(
                max_memory_bytes if max_memory_bytes is not None else resolved.max_memory_bytes
            ),
            max_recursion_depth=(
                max_recursion_depth if max_recursion_depth is not None else resolved.max_recursion_depth
            ),
        )

        self.max_execution_time = effective.max_execution_time
        self.max_memory_bytes = effective.max_memory_bytes
        self.max_recursion_depth = effective.max_recursion_depth

    def _create_limits(self) -> dict[str, Any]:
        return {
            "max_duration_secs": float(self.max_execution_time),
            "max_memory": self.max_memory_bytes,
            "max_recursion_depth": self.max_recursion_depth,
        }

    async def execute(
        self,
        code: str,
        tools: list[Callable[..., Any]],
        agent_id: str,
    ) -> ExecutionResult:
        """Execute agent code with allowlisted tools."""
        start_time = time.time()

        try:
            ctx = MontyContext(
                input_model=EmptyInput,
                tools=tools,
                limits=self._create_limits(),
            )
            result = await ctx.execute_async(code, {})
            duration_ms = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=True,
                return_value=result,
                duration_ms=duration_ms,
                agent_id=agent_id,
            )

        except GrailLimitError as exc:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = str(exc)
            lowered = error_msg.lower()
            if "recursion" in lowered:
                error_type = "recursion"
            elif "memory" in lowered:
                error_type = "memory"
            else:
                error_type = "timeout"
            return ExecutionResult(
                success=False,
                error=error_msg,
                error_type=error_type,
                duration_ms=duration_ms,
                agent_id=agent_id,
            )

        except GrailExecutionError as exc:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = str(exc)
            lowered = error_msg.lower()
            error_type = "syntax" if "syntax" in lowered else "runtime"
            return ExecutionResult(
                success=False,
                error=error_msg,
                error_type=error_type,
                duration_ms=duration_ms,
                agent_id=agent_id,
            )

        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=False,
                error=f"Unexpected error: {str(exc)}",
                error_type="unknown",
                duration_ms=duration_ms,
                agent_id=agent_id,
            )

    def validate_code(self, code: str) -> tuple[bool, Optional[str]]:
        """Validate code syntax without executing."""
        try:
            compile(code, "<string>", "exec")
            return True, None
        except SyntaxError as exc:
            return False, f"Syntax error: {str(exc)}"
