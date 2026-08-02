"""Tests that verify type checking metadata exists."""

from typing import TYPE_CHECKING

from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.runtime.sandbox import SandboxResult

if TYPE_CHECKING:
    _result: SandboxResult
    _summary: str | None = None


def test_type_annotations_present() -> None:
    """Test that key functions have type annotations."""
    assert hasattr(CairnOrchestrator.__init__, "__annotations__")
    assert "executor_factory" in CairnOrchestrator.__init__.__annotations__
