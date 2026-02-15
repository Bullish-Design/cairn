"""MontyContext orchestration for validated sandbox execution."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import inspect
import io
from collections.abc import Callable
from typing import Any, Generic, TypedDict, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import (
    GrailExecutionError,
    GrailLimitError,
    GrailOutputValidationError,
    GrailValidationError,
    extract_location,
    format_runtime_error,
    format_validation_error,
)
from .observability import MetricsCollector, RetryPolicy, StructuredLogger
from .policies import PolicySpec, resolve_effective_limits
from .resource_guard import ResourceGuard, ResourceUsageMetrics
from .snapshots import MontySnapshot
from .stubs import StubGenerator
from .tools import ToolRegistry
from .types import ResourceLimits

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class DebugToolCall(TypedDict):
    name: str
    args: list[Any]
    kwargs: dict[str, Any]
    result: Any


class DebugPayload(TypedDict):
    events: list[str]
    stdout: str
    stderr: str
    tool_calls: list[DebugToolCall]
    resource_metrics: dict[str, Any]


class MontyContext(Generic[InputT, OutputT]):
    """Context for validated execution in Monty with optional tools and typed output."""

    def __init__(
        self,
        input_model: type[InputT],
        limits: ResourceLimits | None = None,
        guard: ResourceGuard | dict[str, Any] | None = None,
        policy: PolicySpec | list[PolicySpec] | tuple[PolicySpec, ...] | None = None,
        output_model: type[OutputT] | None = None,
        tools: list[Callable[..., Any]] | None = None,
        filesystem: Any | None = None,
        debug: bool = False,
        debug_max_output_chars: int | None = None,
        debug_max_tool_calls: int | None = None,
        debug_max_events: int | None = None,
        logger: StructuredLogger | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.input_model = input_model
        self.output_model = output_model
        self.limits = resolve_effective_limits(limits=limits, guard=guard, policy=policy)
        self.tools = ToolRegistry(tools)
        self.filesystem = filesystem
        self.stub_generator = StubGenerator()
        self.debug = debug
        self.debug_max_output_chars = debug_max_output_chars
        self.debug_max_tool_calls = debug_max_tool_calls
        self.debug_max_events = debug_max_events
        self.resource_metrics = ResourceUsageMetrics()
        self.logger = logger or StructuredLogger()
        self.metrics = metrics or MetricsCollector()
        self._debug_payload = self._new_debug_payload()

    @property
    def debug_payload(self) -> DebugPayload:
        return self._debug_payload

    def clear_debug_payload(self) -> None:
        self.resource_metrics = ResourceUsageMetrics()
        self._debug_payload = self._new_debug_payload()

    async def execute_async(self, code: str, inputs: InputT | dict[str, Any]) -> Any | OutputT:
        """Validate input data and execute code in Monty."""
        self._debug_payload = self._new_debug_payload(resource_metrics={})
        self._event("validate-inputs")
        with self.metrics.timer("validate_inputs"):
            validated_inputs = self._validate_inputs(inputs)
        serialized_inputs = validated_inputs.model_dump(mode="python")
        type_stubs = self.stub_generator.generate(
            input_model=self.input_model,
            output_model=self.output_model,
            tools=[self.tools.as_mapping()[name] for name in self.tools.names],
        )

        from pydantic_monty import Monty, run_monty_async

        tools_mapping = self._debug_tools_mapping()
        monty_kwargs = self._supported_kwargs(
            Monty,
            {
                "inputs": ["inputs"],
                "tools": tools_mapping,
                "external_functions": tools_mapping,
                "functions": tools_mapping,
                "globals": tools_mapping,
                "type_definitions": type_stubs,
                "type_check_stubs": type_stubs,
                "stubs": type_stubs,
            },
            drop_required=("code",),
        )
        self._coerce_external_functions(monty_kwargs)

        run_kwargs = self._supported_kwargs(
            run_monty_async,
            {
                "inputs": {"inputs": serialized_inputs},
                "limits": self.limits,
                "tools": tools_mapping,
                "external_functions": tools_mapping,
                "functions": tools_mapping,
                "globals": tools_mapping,
                "os": self.filesystem,
                "filesystem": self.filesystem,
                "os_access": self.filesystem,
            },
            drop_required=("runner", "monty"),
        )

        out = io.StringIO()
        err = io.StringIO()
        try:
            self._event("build-runner")
            with self.metrics.timer("build_runner"):
                runner = Monty(code, **monty_kwargs)
            self._event("execute")
            with self.metrics.timer("execute_async"):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    result = await run_monty_async(runner, **run_kwargs)
            self._capture_resource_metrics(result)
            self.metrics.increment("executions_success")
            return self._validated_output_with_event(result)
        except Exception as exc:  # noqa: BLE001
            self.metrics.increment("executions_failed")
            raise self._normalize_monty_exception(exc) from exc
        finally:
            self._append_debug_stream("stdout", out.getvalue())
            self._append_debug_stream("stderr", err.getvalue())
            self._event("finished")

    async def start(self, code: str, inputs: InputT | dict[str, Any]) -> MontySnapshot:
        """Validate input data and begin execution in resumable mode."""
        self._debug_payload = self._new_debug_payload(resource_metrics={})
        self._event("validate-inputs")
        with self.metrics.timer("validate_inputs"):
            validated_inputs = self._validate_inputs(inputs)
        serialized_inputs = validated_inputs.model_dump(mode="python")
        type_stubs = self.stub_generator.generate(
            input_model=self.input_model,
            output_model=self.output_model,
            tools=[self.tools.as_mapping()[name] for name in self.tools.names],
        )

        from pydantic_monty import Monty

        tools_mapping = self._debug_tools_mapping()
        monty_kwargs = self._supported_kwargs(
            Monty,
            {
                "inputs": ["inputs"],
                "tools": tools_mapping,
                "external_functions": tools_mapping,
                "functions": tools_mapping,
                "globals": tools_mapping,
                "type_definitions": type_stubs,
                "type_check_stubs": type_stubs,
                "stubs": type_stubs,
            },
            drop_required=("code",),
        )
        self._coerce_external_functions(monty_kwargs)

        start_kwargs = self._supported_kwargs(
            Monty.start,
            {
                "inputs": {"inputs": serialized_inputs},
                "limits": self.limits,
                "print_callback": self._print_callback,
                "os": self.filesystem,
                "filesystem": self.filesystem,
                "os_access": self.filesystem,
            },
            drop_required=("self",),
        )
        try:
            self._event("build-runner")
            runner = Monty(code, **monty_kwargs)
            return await self._run_start(runner, start_kwargs)
        except Exception as exc:  # noqa: BLE001
            self.metrics.increment("executions_failed")
            raise self._normalize_monty_exception(exc) from exc

    def load_snapshot(self, serialized: bytes, **kwargs: Any) -> MontySnapshot:
        """Restore a paused snapshot in this context from a serialized payload."""
        from pydantic_monty import MontySnapshot as NativeMontySnapshot

        load_kwargs = self._supported_kwargs(
            NativeMontySnapshot.load,
            {
                "print_callback": self._print_callback,
                "os": self.filesystem,
                "filesystem": self.filesystem,
                "os_access": self.filesystem,
                **kwargs,
            },
            drop_required=(),
        )
        try:
            snapshot = NativeMontySnapshot.load(serialized, **load_kwargs)
        except Exception as exc:  # noqa: BLE001
            self.metrics.increment("executions_failed")
            raise self._normalize_monty_exception(exc) from exc

        self._event("restore-snapshot")
        return MontySnapshot(
            snapshot,
            validate_output=self._validated_output_with_event,
            normalize_exception=self._normalize_monty_exception,
        )

    def execute(self, code: str, inputs: InputT | dict[str, Any]) -> Any | OutputT:
        """Synchronous wrapper around :meth:`execute_async`."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute_async(code, inputs))
        raise RuntimeError(
            "MontyContext.execute() cannot be called from an active event loop; "
            "use MontyContext.execute_async() instead."
        ) from None

    def execute_with_resilience(
        self,
        code: str,
        inputs: InputT | dict[str, Any],
        *,
        retry_policy: RetryPolicy | None = None,
        fallback: Any | None = None,
    ) -> Any | OutputT:
        """Execute with retry/fallback behavior for production degradation paths."""
        policy = retry_policy or RetryPolicy()
        for attempt in range(1, policy.attempts + 1):
            try:
                self.logger.emit("grail.execution.attempt", attempt=attempt)
                return self.execute(code, inputs)
            except Exception as exc:  # noqa: BLE001
                self.logger.emit("grail.execution.failure", attempt=attempt, error=str(exc))
                if policy.should_retry(exc, attempt=attempt):
                    if policy.backoff_seconds > 0:
                        import time

                        time.sleep(policy.backoff_seconds)
                    continue
                if fallback is not None:
                    self.metrics.increment("executions_fallback")
                    self.logger.emit("grail.execution.fallback", attempt=attempt)
                    return fallback
                raise

    def _validate_inputs(self, inputs: InputT | dict[str, Any]) -> InputT:
        if isinstance(inputs, self.input_model):
            return inputs

        try:
            return self.input_model.model_validate(inputs)
        except ValidationError as exc:
            message = format_validation_error(
                f"Input validation failed for {self.input_model.__name__}",
                exc,
            )
            raise GrailValidationError(message) from exc

    def _validate_output(self, output: Any) -> Any | OutputT:
        if self.output_model is None:
            return output
        return self.output_model.model_validate(output)

    async def _run_start(self, runner: Any, start_kwargs: dict[str, Any]) -> MontySnapshot:
        out = io.StringIO()
        err = io.StringIO()
        self._event("execute")
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                state = runner.start(**start_kwargs)
                if inspect.isawaitable(state):
                    state = await state
        finally:
            self._append_debug_stream("stdout", out.getvalue())
            self._append_debug_stream("stderr", err.getvalue())
            self._event("finished")

        return MontySnapshot(
            state,
            validate_output=self._validated_output_with_event,
            normalize_exception=self._normalize_monty_exception,
        )

    def _validated_output_with_event(self, output: Any) -> Any | OutputT:
        self._event("validate-output")
        try:
            return self._validate_output(output)
        except ValidationError as exc:
            output_name = self.output_model.__name__ if self.output_model else "OutputModel"
            raise GrailOutputValidationError(
                format_validation_error(
                    f"Output validation failed for {output_name}",
                    exc,
                )
            ) from exc

    def _normalize_monty_exception(self, exc: Exception) -> Exception:
        from pydantic_monty import MontyError, MontyRuntimeError

        if isinstance(exc, ValidationError):
            output_name = self.output_model.__name__ if self.output_model else "OutputModel"
            return GrailOutputValidationError(
                format_validation_error(
                    f"Output validation failed for {output_name}",
                    exc,
                )
            )
        if isinstance(exc, MontyRuntimeError):
            location = extract_location(exc)
            message = format_runtime_error(
                category="Monty runtime error",
                exc=exc,
                location=location,
            )
            if "limit" in str(exc).lower() or "recursion" in str(exc).lower():
                self.resource_metrics.exceeded.append("runtime_limit")
                self._debug_payload["resource_metrics"] = self.resource_metrics.model_dump(
                    exclude_none=True
                )
                return GrailLimitError(message)
            return GrailExecutionError(message)
        if isinstance(exc, PermissionError):
            return GrailExecutionError(f"Monty filesystem permission denied: {exc}")
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.EACCES:
            return GrailExecutionError(f"Monty filesystem permission denied: {exc}")
        if isinstance(exc, MontyError):
            location = extract_location(exc)
            message = format_runtime_error(
                category="Monty execution failed",
                exc=exc,
                location=location,
            )
            return GrailExecutionError(message)
        return exc

    def _print_callback(self, stream: str, text: str) -> None:
        if stream == "stdout":
            self._append_debug_stream("stdout", text)
        elif stream == "stderr":
            self._append_debug_stream("stderr", text)

    def _coerce_external_functions(self, kwargs: dict[str, Any]) -> None:
        """Convert mapping-style ``external_functions`` to names for older Monty APIs."""
        value = kwargs.get("external_functions")
        if isinstance(value, dict):
            kwargs["external_functions"] = list(value.keys())

    def _supported_kwargs(
        self,
        callable_obj: Callable[..., Any],
        candidates: dict[str, Any],
        drop_required: tuple[str, ...],
    ) -> dict[str, Any]:
        """Filter ``candidates`` by signature unless ``**kwargs`` is accepted, then drop ``drop_required`` names."""
        signature = inspect.signature(callable_obj)
        parameters = signature.parameters

        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
            return candidates

        supported: dict[str, Any] = {}
        for key, value in candidates.items():
            if key in parameters:
                supported[key] = value

        for required in drop_required:
            supported.pop(required, None)

        return supported

    def _event(self, value: str) -> None:
        self.logger.emit("grail.lifecycle", step=value)
        if self.debug:
            self._append_debug_list("events", value, self.debug_max_events)

    def _capture_resource_metrics(self, result: Any) -> None:
        payload = None
        if isinstance(result, dict) and "resource_metrics" in result:
            payload = result["resource_metrics"]
        elif hasattr(result, "resource_metrics"):
            payload = getattr(result, "resource_metrics")
        elif hasattr(result, "metrics"):
            payload = getattr(result, "metrics")

        if payload is None:
            self.resource_metrics = ResourceUsageMetrics()
        else:
            self.resource_metrics = ResourceUsageMetrics.from_runtime_payload(payload)
        self._debug_payload["resource_metrics"] = self.resource_metrics.model_dump(
            exclude_none=True
        ) | {"collector": self.metrics.snapshot()}

    def _debug_tools_mapping(self) -> dict[str, Callable[..., Any]]:
        tools = self.tools.as_mapping()
        if not self.debug:
            return tools

        wrapped: dict[str, Callable[..., Any]] = {}
        for name, fn in tools.items():
            wrapped[name] = self._wrap_tool(name, fn)
        return wrapped

    def _wrap_tool(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        async def _inner(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            self._append_debug_list(
                "tool_calls",
                {"name": name, "args": list(args), "kwargs": kwargs, "result": result},
                self.debug_max_tool_calls,
            )
            return result

        return _inner

    def _new_debug_payload(self, resource_metrics: dict[str, Any] | None = None) -> DebugPayload:
        metrics_payload = (
            self.resource_metrics.model_dump(exclude_none=True)
            if resource_metrics is None
            else resource_metrics
        )
        return {
            "events": [],
            "stdout": "",
            "stderr": "",
            "tool_calls": [],
            "resource_metrics": metrics_payload,
        }

    def _append_debug_stream(self, stream: str, text: str) -> None:
        current = self._debug_payload[stream]
        combined = current + text
        max_chars = self.debug_max_output_chars
        if max_chars is None:
            self._debug_payload[stream] = combined
            return
        if max_chars <= 0:
            self._debug_payload[stream] = ""
            return
        self._debug_payload[stream] = combined[-max_chars:]

    def _append_debug_list(self, key: str, value: Any, max_items: int | None) -> None:
        entries = self._debug_payload[key]
        entries.append(value)
        if max_items is None:
            return
        if max_items <= 0:
            entries.clear()
            return
        overflow = len(entries) - max_items
        if overflow > 0:
            del entries[:overflow]
