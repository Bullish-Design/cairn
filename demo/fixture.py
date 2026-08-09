"""Build the throwaway demo project (guide §P2).

Every fixture entry exists to make exactly one line of the walkthrough true —
no decorative files:

- ``.gitignore`` — "*.log\\nsecrets.env\\n": gitignore-aware admission
- ``secrets.env``  — gitignored: proves admission filtering (never materialized)
- ``README.md``    — ordinary content file
- ``run.sh``       — 0o755: mode preservation through materialization
- ``link_to_main`` — symlink: materialization does not dereference symlinks
- ``empty_dir/``   — empty directories survive materialization
- ``src/main.py``  — the file the agents touch across the walkthrough
- ``src/util.py``  — support module (imported by main.py)
- ``src/doomed.py``— deleted by the ch05/ch10 agent
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from cairn.runtime.repo import capture_manifest

GITIGNORE = "*.log\nsecrets.env\n"

FILES: dict[str, str] = {
    "secrets.env": "SECRET_TOKEN=demo-secret-that-must-not-reach-the-sandbox\n",
    "README.md": (
        "# demo\n\n"
        "A throwaway project that the Cairn walkthrough drives real agents against.\n\n"
        "## Running\n\n    ./run.sh\n"
    ),
    "src/main.py": (
        '"""The demo app entry point."""\n'
        "\n"
        "from src.util import greet\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        '    print(greet("world"))\n'
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    ),
    "src/util.py": (
        '"""Shared helpers for the demo app."""\n\n\ndef greet(name: str) -> str:\n    return f"hello, {name}"\n'
    ),
    "src/doomed.py": (
        '"""This module is deleted by the walkthrough agent."""\n'
        "\n"
        "\n"
        "def legacy() -> str:\n"
        '    return "legacy behavior"\n'
    ),
}

RUN_SH = '#!/bin/sh\nset -eu\necho "demo app: $(python3 src/main.py)"\n'


def build(project_root: Path) -> Path:
    """Create the fixture tree, git init it, and return the root."""
    project_root = Path(project_root)
    project_root.mkdir(parents=True, exist_ok=True)

    (project_root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    for rel, content in FILES.items():
        target = project_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    run_sh = project_root / "run.sh"
    run_sh.write_text(RUN_SH, encoding="utf-8")
    run_sh.chmod(0o755)

    empty_dir = project_root / "empty_dir"
    empty_dir.mkdir(parents=True, exist_ok=True)

    link = project_root / "link_to_main"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to("src/main.py")

    _git_init(project_root)
    return project_root


def _git_init(project_root: Path) -> None:
    """``git init`` + ``git add -A`` — the filter reads ``.gitignore``, not
    the index, so no commit is needed."""
    subprocess.run(["git", "init", "-q", str(project_root)], check=True)
    subprocess.run(["git", "-C", str(project_root), "add", "-A"], check=True)


def tree_digest(project_root: Path) -> str:
    """A stable content digest of the canonical tree (manifest-faithful).

    Used for the ch01 baseline and the ch04 'the tree is untouched' headline:
    the digest must change exactly when the *admitted* tree changes.
    """
    manifest = capture_manifest(project_root)
    hasher = hashlib.sha256()
    for rel in sorted(manifest.entries):
        entry = manifest.entries[rel]
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(entry.kind.encode("utf-8"))
        hasher.update(b"\x00")
        if entry.kind == "file":
            hasher.update((entry.digest or "").encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(str(entry.mode or 0).encode("utf-8"))
        elif entry.kind == "symlink":
            hasher.update((entry.link_target or "").encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()[:16]
