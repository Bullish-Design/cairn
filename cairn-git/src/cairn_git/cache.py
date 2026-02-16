"""Cache helpers for Git-based code providers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class GitReference:
    repo_url: str
    file_path: str
    ref: str | None


def parse_git_reference(reference: str) -> GitReference:
    parsed = urlparse(reference)
    if parsed.scheme != "git":
        raise ValueError("Git references must start with git://")

    repo_url = _build_repo_url(parsed)
    file_path = parsed.fragment
    if not file_path:
        raise ValueError("Git references must include a file path fragment")

    query = parse_qs(parsed.query)
    ref = query.get("ref", [None])[0]

    return GitReference(repo_url=repo_url, file_path=file_path, ref=ref)


def _build_repo_url(parsed) -> str:
    if parsed.netloc:
        return f"https://{parsed.netloc}{parsed.path}"

    return parsed.path


def ensure_repo_cache(reference: GitReference, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    repo_hash = hashlib.sha256(reference.repo_url.encode("utf-8")).hexdigest()
    repo_path = cache_dir / repo_hash

    if not repo_path.exists():
        _run_git(["clone", "--depth", "1", reference.repo_url, str(repo_path)])

    if reference.ref:
        _run_git(["-C", str(repo_path), "fetch", "--depth", "1", "origin", reference.ref])
        _run_git(["-C", str(repo_path), "checkout", reference.ref])

    return repo_path


def _run_git(args: list[str]) -> None:
    subprocess.run(["git", *args], check=True)
