"""Error types and formatting helpers for Grail."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError


class GrailExecutionError(RuntimeError):
    """Raised when Monty fails during code execution."""


class GrailValidationError(ValueError):
    """Raised when Grail input validation fails."""


class GrailLimitError(GrailExecutionError):
    """Raised when execution appears to violate configured limits."""


class GrailOutputValidationError(GrailValidationError):
    """Raised when output validation against the configured output model fails."""


@dataclass(slots=True)
class ErrorLocation:
    """Optional source location details for user-facing errors."""

    line: int | None = None
    column: int | None = None


def format_validation_error(prefix: str, exc: ValidationError) -> str:
    """Render a concise pydantic validation error with dotted field paths."""
    details: list[str] = []
    for item in exc.errors():
        path = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        details.append(f"{path}: {item.get('msg', 'invalid value')}")
    if not details:
        return prefix
    return f"{prefix}: {'; '.join(details)}"


def format_runtime_error(
    *,
    category: str,
    exc: Exception,
    location: ErrorLocation | None = None,
) -> str:
    """Create a normalized runtime error message with optional line info."""
    where = ""
    if location and location.line is not None:
        where = (
            f" (line {location.line}"
            + (f", col {location.column}" if location.column else "")
            + ")"
        )
    return f"{category}{where}: {exc}"


def user_traceback_lines(tb: str) -> list[str]:
    """Drop internal Grail frames from a traceback string."""
    lines = tb.splitlines()
    return [line for line in lines if "src/grail/" not in line and "grail/context.py" not in line]


def extract_location(data: Any) -> ErrorLocation:
    """Best-effort extraction of line/column info from Monty-like exceptions."""
    line = getattr(data, "lineno", None) or getattr(data, "line", None)
    column = getattr(data, "offset", None) or getattr(data, "column", None)
    return ErrorLocation(line=line, column=column)
