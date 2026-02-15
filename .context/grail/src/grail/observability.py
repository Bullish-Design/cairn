"""Production observability helpers for Grail."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RetryPolicy:
    """Retry settings for resilient execution wrappers."""

    attempts: int = 1
    backoff_seconds: float = 0.0
    retry_on: tuple[type[Exception], ...] = (Exception,)

    def should_retry(self, error: Exception, *, attempt: int) -> bool:
        if attempt >= self.attempts:
            return False
        return isinstance(error, self.retry_on)


class MetricsCollector:
    """Simple in-memory metrics collector with counter/timer primitives."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._timings_ms: dict[str, list[float]] = {}

    def increment(self, name: str, value: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + value

    def observe_ms(self, name: str, duration_ms: float) -> None:
        self._timings_ms.setdefault(name, []).append(duration_ms)

    @contextmanager
    def timer(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.observe_ms(name, elapsed_ms)

    def snapshot(self) -> dict[str, Any]:
        timings: dict[str, dict[str, float]] = {}
        for name, values in self._timings_ms.items():
            if not values:
                continue
            timings[name] = {
                "count": float(len(values)),
                "min_ms": min(values),
                "max_ms": max(values),
                "avg_ms": sum(values) / len(values),
            }
        return {"counters": dict(self._counters), "timings": timings}


class StructuredLogger:
    """Default JSON-compatible logger hook used by :class:`MontyContext`."""

    def __init__(self, sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._sink = sink or (lambda _: None)

    def emit(self, event: str, **payload: Any) -> None:
        self._sink({"event": event, **payload})
