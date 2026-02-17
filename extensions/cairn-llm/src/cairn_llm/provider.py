"""LLM-backed code provider for Cairn."""

from __future__ import annotations

from typing import Any

from cairn.core.exceptions import CodeProviderError
from cairn.providers.providers import CodeProvider
from cairn_llm.prompts import DEFAULT_PROMPT


class LLMCodeProvider(CodeProvider):
    """Generate Grail `.pym` code using an external LLM service."""

    def __init__(self, prompt_template: str | None = None) -> None:
        self.prompt_template = prompt_template or DEFAULT_PROMPT

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        _ = context
        if not reference.strip():
            raise CodeProviderError("LLM task prompt must be non-empty")
        return self.prompt_template.format(task=reference.strip())

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        if not code.strip():
            return False, "Generated code is empty"
        if "Input(" not in code:
            return False, "Generated code must include at least one Input()"
        if "@external" not in code:
            return False, "Generated code must define at least one external tool"
        return True, None
