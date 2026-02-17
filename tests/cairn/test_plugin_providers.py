from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
for rel_path in (
    "extensions/cairn-llm/src",
    "extensions/cairn-git/src",
    "extensions/cairn-registry/src",
):
    sys.path.append(str(ROOT / rel_path))

from cairn_git.cache import GitReference
from cairn_git.provider import GitCodeProvider
from cairn_llm.provider import LLMCodeProvider
from cairn_registry.provider import RegistryCodeProvider


@pytest.mark.asyncio
async def test_llm_provider_generates_prompt() -> None:
    provider = LLMCodeProvider()
    code = await provider.get_code("Add docstrings", {})

    assert 'Input("task_description")' in code
    assert "@external" in code
    assert "submit_result" in code
    assert await provider.validate_code(code) == (True, None)


@pytest.mark.asyncio
async def test_git_provider_reads_cached_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    script_path = repo_dir / "tasks" / "cleanup.pym"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("x = 1", encoding="utf-8")

    def fake_parse(_: str) -> GitReference:
        return GitReference(repo_url="https://example.com/repo", file_path="tasks/cleanup.pym", ref=None)

    def fake_cache(_: GitReference, __: Path) -> Path:
        return repo_dir

    monkeypatch.setattr("cairn_git.provider.parse_git_reference", fake_parse)
    monkeypatch.setattr("cairn_git.provider.ensure_repo_cache", fake_cache)

    provider = GitCodeProvider(cache_dir=tmp_path / "cache")
    code = await provider.get_code("git://example.com/repo#tasks/cleanup.pym", {})

    assert code == "x = 1"


@pytest.mark.asyncio
async def test_registry_provider_fetches_code(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class StubClient:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def fetch_code(self, path: str) -> str:
            calls.append((self.base_url, path))
            return "registry code"

    monkeypatch.setattr("cairn_registry.provider.RegistryClient", StubClient)

    provider = RegistryCodeProvider(base_url="https://registry.example.com")
    code = await provider.get_code("scripts/format.pym", {})

    assert code == "registry code"
    assert calls == [("https://registry.example.com", "scripts/format.pym")]
