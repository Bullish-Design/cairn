"""Error hierarchy for grail."""

from typing import Any


class GrailError(Exception):
    """Base exception for all grail errors."""


class ParseError(GrailError):
    """Raised when .pym file has Python syntax errors."""

    def __init__(
        self,
        message: str,
        lineno: int | None = None,
        col_offset: int | None = None,
    ) -> None:
        self.message = message
        self.lineno = lineno
        self.col_offset = col_offset
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        if self.lineno is not None:
            return f"Syntax error at line {self.lineno}: {self.message}"
        return f"Syntax error: {self.message}"


class CheckError(GrailError):
    """Raised when @external or Input() declarations are malformed."""

    def __init__(self, message: str, lineno: int | None = None) -> None:
        self.message = message
        self.lineno = lineno
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        if self.lineno is not None:
            return f"Declaration error at line {self.lineno}: {self.message}"
        return f"Declaration error: {self.message}"


class InputError(GrailError):
    """Raised when runtime inputs don't match declared Input() specs."""

    def __init__(self, message: str, input_name: str | None = None) -> None:
        self.message = message
        self.input_name = input_name
        super().__init__(message)


class ExternalError(GrailError):
    """Raised when external functions aren't provided or don't match declarations."""

    def __init__(self, message: str, function_name: str | None = None) -> None:
        self.message = message
        self.function_name = function_name
        super().__init__(message)


class ExecutionError(GrailError):
    """Raised when Monty runtime error occurs."""

    def __init__(
        self,
        message: str,
        lineno: int | None = None,
        col_offset: int | None = None,
        source_context: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        self.message = message
        self.lineno = lineno
        self.col_offset = col_offset
        self.source_context = source_context
        self.suggestion = suggestion
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts: list[str] = []
        if self.lineno is not None:
            parts.append(f"Line {self.lineno}")
        parts.append(self.message)

        if self.source_context and self.lineno is not None:
            context_lines = self._build_context_display(
                source=self.source_context,
                error_line=self.lineno,
                context=2,
            )
            parts.append("")
            parts.append(context_lines)

        if self.suggestion:
            parts.append(f"Suggestion: {self.suggestion}")

        return "\n".join(parts)

    def _build_context_display(self, source: str, error_line: int, context: int = 2) -> str:
        lines = source.splitlines()
        start = max(0, error_line - context - 1)
        end = min(len(lines), error_line + context)

        output = []
        for i in range(start, end):
            line_num = i + 1
            prefix = "> " if line_num == error_line else "  "
            output.append(f"{prefix}{line_num:>4} | {lines[i]}")
        return "\n".join(output)


class LimitError(ExecutionError):
    """Raised when resource limits are exceeded."""

    def __init__(self, message: str, limit_type: str | None = None) -> None:
        self.limit_type = limit_type
        super().__init__(message)


class OutputError(GrailError):
    """Raised when output validation against output_model fails."""

    def __init__(self, message: str, validation_errors: Any = None) -> None:
        self.message = message
        self.validation_errors = validation_errors
        super().__init__(message)
