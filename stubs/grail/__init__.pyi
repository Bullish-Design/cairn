"""Type stubs for grail module."""

from typing import Any, Protocol

class GrailScript(Protocol):
    """Protocol for Grail script objects."""

    def check(self) -> Any:
        """Validate the Grail script."""

    async def run(self, inputs: dict[str, Any], externals: dict[str, Any]) -> dict[str, Any]:
        """Run the Grail script."""

class GrailExecutionError(Exception):
    """Grail script execution error."""

class InputError(Exception):
    """Grail input validation error."""

def load(script_path: str) -> GrailScript:
    """Load a Grail script (legacy API)."""
