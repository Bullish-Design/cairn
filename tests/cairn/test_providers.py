from __future__ import annotations

from pathlib import Path

import pytest

import cairn.providers as providers
from cairn.providers import CodeProviderError, FileCodeProvider, InlineCodeProvider, resolve_code_provider


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


class DummyEntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        return self._target


class DummyProvider:
    def __init__(self, project_root: Path | None = None, base_path: Path | None = None) -> None:
        self.project_root = project_root
        self.base_path = base_path

    async def get_code(self, reference: str, context: dict[str, object]) -> str:
        _ = context
        return reference

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return True, None


def test_resolve_provider_from_entrypoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_entry_points(group: str):
        assert group == "cairn.providers"
        return [DummyEntryPoint("dummy", DummyProvider)]

    monkeypatch.setattr(providers.metadata, "entry_points", fake_entry_points)

    provider = resolve_code_provider("dummy", project_root=tmp_path, base_path=None)

    assert isinstance(provider, DummyProvider)
    assert provider.project_root == tmp_path


def test_resolve_provider_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers.metadata, "entry_points", lambda group: [])

    with pytest.raises(CodeProviderError):
        resolve_code_provider("missing", project_root=None, base_path=None)
