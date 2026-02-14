"""Agent code executor using Monty sandbox.

This module wraps Monty sandbox execution with proper error handling,
resource limits, and result tracking.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

import pydantic_monty
from pydantic_monty import ResourceLimits

from cairn.settings import ExecutorSettings


@dataclass
class ExecutionResult:
    """Result of agent code execution.

    Attributes:
        success: Whether execution completed successfully
        return_value: Return value from code (if successful)
        error: Error message (if failed)
        error_type: Type of error (syntax, runtime, timeout, memory)
        duration_ms: Execution duration in milliseconds
        agent_id: Agent identifier
    """

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
    """Executes agent code in Monty sandbox with resource limits."""

    def __init__(
        self,
        max_execution_time: float | None = None,
        max_memory_bytes: int | None = None,
        max_recursion_depth: int | None = None,
        settings: ExecutorSettings | None = None,
    ):
        """Initialize executor with resource limits.

        Args:
            max_execution_time: Maximum execution time in seconds (default: 60)
            max_memory_bytes: Maximum memory in bytes (default: 100MB)
            max_recursion_depth: Maximum recursion depth (default: 1000)
        """
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

    def _create_limits(self) -> ResourceLimits:
        """Create resource limits for Monty.

        Returns:
            ResourceLimits configuration
        """
        return {
            "max_duration_secs": float(self.max_execution_time),
            "max_memory": self.max_memory_bytes,
            "max_recursion_depth": self.max_recursion_depth,
        }

    async def execute(
        self,
        code: str,
        external_functions: dict[str, Any],
        agent_id: str,
    ) -> ExecutionResult:
        """Execute agent code with external functions.

        Args:
            code: Python code to execute
            external_functions: Dictionary of external functions
            agent_id: Agent identifier

        Returns:
            ExecutionResult with success/failure info
        """
        start_time = time.time()

        try:
            # Create Monty instance
            m = pydantic_monty.Monty(
                code,
                inputs=[],
                external_functions=list(external_functions.keys()),
                script_name=f"{agent_id}.py",
            )

            # Run with resource limits
            limits = self._create_limits()
            result = await pydantic_monty.run_monty_async(
                m,
                inputs=None,
                external_functions=external_functions,
                limits=limits,
            )

            duration_ms = (time.time() - start_time) * 1000

            return ExecutionResult(
                success=True,
                return_value=result,
                duration_ms=duration_ms,
                agent_id=agent_id,
            )

        except pydantic_monty.MontySyntaxError as e:
            duration_ms = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=False,
                error=str(e),
                error_type="syntax",
                duration_ms=duration_ms,
                agent_id=agent_id,
            )

        except pydantic_monty.MontyRuntimeError as e:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = str(e)

            # Detect specific error types
            if "timeout" in error_msg.lower() or "duration" in error_msg.lower():
                error_type = "timeout"
            elif "memory" in error_msg.lower():
                error_type = "memory"
            elif "recursion" in error_msg.lower():
                error_type = "recursion"
            else:
                error_type = "runtime"

            return ExecutionResult(
                success=False,
                error=error_msg,
                error_type=error_type,
                duration_ms=duration_ms,
                agent_id=agent_id,
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=False,
                error=f"Unexpected error: {str(e)}",
                error_type="unknown",
                duration_ms=duration_ms,
                agent_id=agent_id,
            )

    def validate_code(self, code: str) -> tuple[bool, Optional[str]]:
        """Validate code syntax without executing.

        Args:
            code: Python code to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            compile(code, "<string>", "exec")
            return True, None
        except SyntaxError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Validation error: {str(e)}"
