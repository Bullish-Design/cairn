from __future__ import annotations

from pathlib import Path

import pytest

import cairn.providers.providers as providers
from cairn.core.exceptions import ProviderError
from cairn.providers.providers import FileCodeProvider, InlineCodeProvider, resolve_code_provider


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

    with pytest.raises(ProviderError):
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

    with pytest.raises(ProviderError):
        resolve_code_provider("missing", project_root=None, base_path=None)


@pytest.mark.asyncio
async def test_file_provider_retries_transient_read_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code_path = tmp_path / "task.pym"
    code_path.write_text("x = 1", encoding="utf-8")

    calls = {"count": 0}
    original_read_text = Path.read_text

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == code_path and calls["count"] < 2:
            calls["count"] += 1
            raise ConnectionError("transient")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    provider = FileCodeProvider(base_path=tmp_path)
    code = await provider.get_code("task", {})

    assert code == "x = 1"
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_file_provider_fails_fast_for_non_retryable_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code_path = tmp_path / "task.pym"
    code_path.write_text("x = 1", encoding="utf-8")

    calls = {"count": 0}

    def permanent_failure(self: Path, *args: object, **kwargs: object) -> str:
        _ = args
        _ = kwargs
        if self == code_path:
            calls["count"] += 1
            raise ValueError("bad encoding")
        raise AssertionError("Unexpected path")

    monkeypatch.setattr(Path, "read_text", permanent_failure)

    provider = FileCodeProvider(base_path=tmp_path)

    with pytest.raises(ProviderError, match="Failed to read code"):
        await provider.get_code("task", {})

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_file_provider_retry_exhaustion_raises_last_connection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code_path = tmp_path / "task.pym"
    code_path.write_text("x = 1", encoding="utf-8")

    calls = {"count": 0}

    def always_fails(self: Path, *args: object, **kwargs: object) -> str:
        _ = args
        _ = kwargs
        if self == code_path:
            calls["count"] += 1
            raise ConnectionError(f"temporary-{calls['count']}")
        raise AssertionError("Unexpected path")

    monkeypatch.setattr(Path, "read_text", always_fails)

    provider = FileCodeProvider(base_path=tmp_path)

    with pytest.raises(ConnectionError, match="temporary-3"):
        await provider.get_code("task", {})

    assert calls["count"] == 3
