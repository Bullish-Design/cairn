from __future__ import annotations

from pathlib import Path

import pytest

from cairn.providers import CodeProviderError, FileCodeProvider, InlineCodeProvider


@pytest.mark.asyncio
async def test_inline_provider_returns_reference() -> None:
    provider = InlineCodeProvider()
    code = await provider.get_code("print('hi')", {})

    assert code == "print('hi')"
    assert await provider.validate_code(code) == (True, None)


@pytest.mark.asyncio
async def test_file_provider_reads_pym(tmp_path: Path) -> None:
    code_path = tmp_path / "task.pym"
    code_path.write_text("x = 1", encoding="utf-8")

    provider = FileCodeProvider(base_path=tmp_path)
    code = await provider.get_code("task", {})

    assert code == "x = 1"


@pytest.mark.asyncio
async def test_file_provider_missing_reference_raises(tmp_path: Path) -> None:
    provider = FileCodeProvider(base_path=tmp_path)

    with pytest.raises(CodeProviderError):
        await provider.get_code("missing", {})
