"""Real repository snapshots and disposable workspace materialization.

The actual Git working tree is the canonical source of truth (review §4.2);
``stable.db`` as a file mirror is gone.  This module provides the three pieces
the rest of the runtime builds on:

- :class:`ProjectFilter` — a gitignore-aware inclusion predicate confined
  beneath the repository root.  It never follows symlinks and never admits
  VCS metadata (``.git``/``.hg``/``.jj``), host scaffolding (``.cairn``,
  ``.agentfs``), or ignored paths.
- :func:`capture_manifest` — a faithful snapshot of the tree: existence,
  kind (file/dir/symlink), content digest, permission bits, and symlink
  target.  An *absent* state is simply "not present in the manifest"; empty
  directories are recorded explicitly.
- :func:`materialize_workspace` — creates the disposable real directory the
  agent runs over, using copy-on-write/reflinks where the filesystem supports
  them and a plain copy otherwise.  Modes, symlinks, and empty directories
  are preserved so the agent sees a faithful view of the repo.

Nothing in this module ever dereferences a symlink: every entry is read via
``lstat`` and files are opened with ``O_NOFOLLOW``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pathspec import GitIgnoreSpec
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Directory names that are never part of the agent's view of the repo.
# VCS metadata must not leak into the disposable workspace; host scaffolding
# (``.cairn``) and Cairn's own metadata (``.agentfs``) must never be
# materialized or re-applied.  Developer-environment dirs are excluded so a
# Nix/venv build closure is never copied into a disposable workspace; add
# more via ``OrchestratorSettings.extra_ignore_dirs``.
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        # VCS metadata
        ".git",
        ".hg",
        ".svn",
        ".jj",
        # Cairn / host scaffolding
        ".cairn",
        ".agentfs",
        # Developer environments (usually gitignored; explicit for safety)
        ".devenv",
        ".direnv",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".hypothesis",
        ".tox",
        ".nox",
        ".idea",
        ".vscode",
        ".eggs",
        "dist",
        "build",
        "target",
        "htmlcov",
    }
)

# File suffixes excluded from the snapshot by name (never imported, never
# materialized).  Kept deliberately small: the manifest is a *faithful* view
# of the repo, so ordinary build artifacts are only excluded when they carry
# no content meaning for the agent.
EXCLUDED_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo"})


class ManifestEntry(BaseModel):
    """One path in a repository manifest (faithful, no-follow snapshot)."""

    path: str = Field(description="Repository-relative posix path")
    kind: Literal["file", "dir", "symlink"]
    size: int | None = Field(default=None, description="Byte size (files only)")
    digest: str | None = Field(default=None, description="sha256 hex (files only)")
    mode: int | None = Field(default=None, description="Permission bits (st_mode & 0o7777)")
    link_target: str | None = Field(default=None, description="Raw readlink text (symlinks only)")


class Manifest(BaseModel):
    """A faithful point-in-time snapshot of a repository tree."""

    captured_at: float = Field(default_factory=time.time)
    entries: dict[str, ManifestEntry] = Field(default_factory=dict)

    def entry_for(self, rel: str) -> ManifestEntry | None:
        return self.entries.get(rel)

    def files(self) -> dict[str, ManifestEntry]:
        return {rel: entry for rel, entry in self.entries.items() if entry.kind == "file"}

    def dirs(self) -> dict[str, ManifestEntry]:
        return {rel: entry for rel, entry in self.entries.items() if entry.kind == "dir"}

    def symlinks(self) -> dict[str, ManifestEntry]:
        return {rel: entry for rel, entry in self.entries.items() if entry.kind == "symlink"}


class ProjectFilter:
    """Inclusion predicate for the repository snapshot.

    A path is admissible iff it lives beneath ``project_root`` (no traversal),
    is not a symlink (symlinks are recorded, never followed), is not an
    excluded directory or suffix, and is not excluded by any applicable
    ``.gitignore`` file (root plus nested, deepest pattern wins).
    """

    def __init__(
        self,
        project_root: Path | str,
        *,
        extra_ignore_dirs: list[str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        extra = set(extra_ignore_dirs or ())
        self._excluded_dirs = EXCLUDED_DIR_NAMES | extra
        self._specs: list[tuple[Path, GitIgnoreSpec]] = []
        self._load_gitignores()

    def _load_gitignores(self) -> None:
        """Collect every ``.gitignore`` beneath the root (shallowest first)."""
        for base in sorted(self.project_root.rglob(".gitignore")):
            if not base.is_file():
                continue
            try:
                lines = base.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            rel_base = base.parent.relative_to(self.project_root)
            self._specs.append((rel_base, GitIgnoreSpec.from_lines(lines)))

    def _gitignored(self, rel: str) -> bool:
        """True if the deepest matching ``.gitignore`` pattern excludes ``rel``."""
        rel_parts = Path(rel).parts
        # Deepest spec first: its decision wins for paths in its subtree.
        for rel_base, spec in reversed(self._specs):
            if rel_base == Path("."):
                sub = rel
            else:
                if len(rel_parts) <= len(rel_base.parts) or rel_parts[: len(rel_base.parts)] != rel_base.parts:
                    continue
                sub = "/".join(rel_parts[len(rel_base.parts) :])
            result = spec.check_file(sub)
            if result.include is not None:  # matched (ignore or negation) — decides
                return result.include
        return False

    def allows_rel(self, rel: str) -> bool:
        """Admissibility for a project-relative posix path (no ``..``)."""
        if rel == "" or rel.startswith("/") or ".." in rel.split("/"):
            return False
        if any(part in self._excluded_dirs for part in Path(rel).parts):
            return False
        if Path(rel).suffix in EXCLUDED_SUFFIXES:
            return False
        return not self._gitignored(rel)

    def allows(self, path: Path) -> bool:
        """Admissibility for an absolute path; confined beneath the root.

        Uses a lexical (non-resolving) comparison so a symlink *inside* the
        root that points outside is still admissible as a symlink entry —
        it is recorded, never dereferenced.
        """
        try:
            rel = path.absolute().relative_to(self.project_root)
        except ValueError:
            return False
        return self.allows_rel(rel.as_posix())


@dataclass(frozen=True)
class ManifestDiff:
    """Differences between a base manifest and the current state."""

    added: list[str]
    removed: list[str]
    modified: list[str]
    mode_changed: list[str]

    @property
    def changed(self) -> list[str]:
        return sorted(set(self.added) | set(self.removed) | set(self.modified))

    @property
    def written(self) -> list[str]:
        """Paths that now exist with different content/kind than at base."""
        return sorted(set(self.added) | set(self.modified))


def diff_manifests(base: Manifest, current: Manifest) -> ManifestDiff:
    """Git-compatible-ish comparison of two manifests.

    ``added``/``removed`` are membership changes; ``modified`` is same-path
    content or kind changes (file digest, symlink target); ``mode_changed``
    is permission-only drift (same content, different bits).
    """
    added: list[str] = []
    removed: list[str] = []
    modified: list[str] = []
    mode_changed: list[str] = []
    for rel, entry in current.entries.items():
        base_entry = base.entries.get(rel)
        if base_entry is None:
            added.append(rel)
            continue
        if _content_sig(entry) != _content_sig(base_entry):
            modified.append(rel)
        elif entry.kind == "file" and entry.mode != base_entry.mode:
            mode_changed.append(rel)
    for rel in base.entries:
        if rel not in current.entries:
            removed.append(rel)
    return ManifestDiff(
        added=sorted(added),
        removed=sorted(removed),
        modified=sorted(modified),
        mode_changed=sorted(mode_changed),
    )


def _content_sig(entry: ManifestEntry) -> tuple[str, str | None]:
    """Content identity: (kind, digest-or-link-target)."""
    return entry.kind, entry.digest if entry.kind == "file" else entry.link_target


def _sha256_file(path: Path) -> str:
    """Hash a regular file without ever following a symlink (O_NOFOLLOW)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        fd = -1  # pragma: no cover - fdopen failure closes fd
        raise
    return digest.hexdigest()


def _walk(root: Path):
    """Non-recursive scandir walk that never descends into symlinked dirs."""
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as iterator:
            for entry in iterator:
                yield Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))


def capture_manifest(root: Path | str, *, filter: ProjectFilter | None = None) -> Manifest:
    """Faithful snapshot of ``root``: files (digest/mode/size), dirs (incl.
    empty), and symlinks (target/mode).  No symlink is ever dereferenced."""
    root = Path(root).resolve()
    filter = filter or ProjectFilter(root)
    entries: dict[str, ManifestEntry] = {}
    for path in _walk(root):
        if not filter.allows(path):
            continue
        rel = path.relative_to(root).as_posix()
        st = path.lstat()
        mode = stat.S_IMODE(st.st_mode)
        if stat.S_ISLNK(st.st_mode):
            entries[rel] = ManifestEntry(path=rel, kind="symlink", mode=mode, link_target=os.readlink(path))
        elif stat.S_ISDIR(st.st_mode):
            entries[rel] = ManifestEntry(path=rel, kind="dir", mode=mode)
        elif stat.S_ISREG(st.st_mode):
            entries[rel] = ManifestEntry(
                path=rel,
                kind="file",
                size=st.st_size,
                digest=_sha256_file(path),
                mode=mode,
            )
    return Manifest(entries=entries)


def _copy_file(src: Path, dst: Path) -> None:
    """Copy one file, preferring copy_file_range (CoW on btrfs/xfs/zfs)."""
    try:
        with src.open("rb") as fin, dst.open("wb") as fout:
            while True:
                written = os.copy_file_range(fin.fileno(), fout.fileno(), 64 * 1024 * 1024)
                if written <= 0:
                    break
        return
    except (OSError, NotImplementedError, ValueError):
        dst.unlink(missing_ok=True)
    with src.open("rb") as fin, dst.open("wb") as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)


def materialize_workspace(
    src_root: Path | str,
    dst_root: Path | str,
    *,
    filter: ProjectFilter | None = None,
) -> int:
    """Create ``dst_root`` as a disposable real copy of ``src_root``.

    Only admissible paths are copied.  Symlinks are recreated as symlinks
    (never dereferenced), permission bits and empty directories are
    preserved.  Returns the number of regular files materialized.

    ``dst_root`` must not exist (or must be an empty directory); callers
    remove it before re-materializing.
    """
    src_root = Path(src_root).resolve()
    dst_root = Path(dst_root)
    filter = filter or ProjectFilter(src_root)
    if dst_root.exists():
        if any(dst_root.iterdir()):
            raise ValueError(f"Materialize target is not empty: {dst_root}")
    else:
        dst_root.mkdir(parents=True)
    file_count = 0
    for path in _walk(src_root):
        if not filter.allows(path):
            continue
        rel = path.relative_to(src_root)
        target = dst_root / rel
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(path), target)
        elif stat.S_ISDIR(st.st_mode):
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, stat.S_IMODE(st.st_mode))
        elif stat.S_ISREG(st.st_mode):
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(path, target)
            os.chmod(target, stat.S_IMODE(st.st_mode))
            file_count += 1
    return file_count
