from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for rel_path in (
    "extensions/cairn-git/src",
    "extensions/cairn-registry/src",
):
    sys.path.append(str(ROOT / rel_path))

from typing import Self

from cairn_git.cache import GitReference
from cairn_git.provider import GitCodeProvider
from cairn_registry.provider import RegistryCodeProvider

from cairn.core.exceptions import CodeProviderError


@pytest.mark.asyncio
async def test_git_provider_reads_cached_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    script_path = repo_dir / "tasks" / "cleanup.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("x = 1", encoding="utf-8")

    def fake_parse(_: str) -> GitReference:
        return GitReference(repo_url="https://example.com/repo", file_path="tasks/cleanup.py", ref=None)

    def fake_cache(_: GitReference, __: Path) -> Path:
        return repo_dir

    monkeypatch.setattr("cairn_git.provider.parse_git_reference", fake_parse)
    monkeypatch.setattr("cairn_git.provider.ensure_repo_cache", fake_cache)

    provider = GitCodeProvider(cache_dir=tmp_path / "cache")
    code = await provider.get_code("git://example.com/repo#tasks/cleanup.py", {})

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
    code = await provider.get_code("scripts/format.py", {})

    assert code == "registry code"
    assert calls == [("https://registry.example.com", "scripts/format.py")]


@pytest.mark.asyncio
async def test_git_provider_refuses_traversal_fragments(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Review §3.5: the file fragment must be confined beneath the cloned
    repo; ``../`` fragments must not read host files."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "ok.py").write_text("x = 1", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("HOST_FILE", encoding="utf-8")

    def fake_parse(_: str) -> GitReference:
        return GitReference(repo_url="https://example.com/repo", file_path="../outside.py", ref=None)

    def fake_cache(_: GitReference, __: Path) -> Path:
        return repo_dir

    monkeypatch.setattr("cairn_git.provider.parse_git_reference", fake_parse)
    monkeypatch.setattr("cairn_git.provider.ensure_repo_cache", fake_cache)

    provider = GitCodeProvider(cache_dir=tmp_path / "cache")
    with pytest.raises(CodeProviderError):
        await provider.get_code("git://example.com/repo#../outside.py", {})


@pytest.mark.asyncio
async def test_git_clone_runs_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review §3.5: git subprocesses must run with a timeout so a hung remote
    cannot block the async provider forever."""
    import cairn_git.cache as git_cache

    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(git_cache.subprocess, "run", fake_run)

    git_cache._run_git(["clone", "--depth", "1", "https://example.com/repo", "/tmp/repo"])

    assert captured["args"][0] == "git"
    assert captured["kwargs"].get("timeout") is not None


@pytest.mark.asyncio
async def test_registry_client_fetch_has_timeout_and_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review §3.5: registry fetches must carry a timeout and must not read
    unbounded response bodies into orchestrator memory."""
    import cairn_registry.client as reg_client

    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self, *args: object, **kwargs: object) -> bytes:
            return self._payload

    class FakeOpener:
        def __call__(self, url: str, *args: object, **kwargs: object) -> FakeResponse:
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse(b"x" * (8 * 1024 * 1024))  # 8 MB, over any sane cap

    monkeypatch.setattr(reg_client.urllib.request, "urlopen", FakeOpener())

    client = reg_client.RegistryClient(base_url="https://registry.example.com")
    with pytest.raises(CodeProviderError):
        client.fetch_code("scripts/format.py")

    assert captured["kwargs"].get("timeout") is not None
