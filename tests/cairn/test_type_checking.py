"""Tests that verify type checking metadata exists."""

from typing import TYPE_CHECKING

from cairn.runtime.external_functions import create_external_functions
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.core.types import ExternalTools


if TYPE_CHECKING:
    tools: ExternalTools
    _matches = tools["search_content"]("needle", "src")


def test_type_annotations_present() -> None:
    """Test that key functions have type annotations."""
    assert hasattr(create_external_functions, "__annotations__")
    assert "return" in create_external_functions.__annotations__

    assert hasattr(CairnOrchestrator.__init__, "__annotations__")
    assert "tools_factory" in CairnOrchestrator.__init__.__annotations__
