"""Repository snapshot + disposable workspace tests (review §2.4, §2.5, §3.3).

The old ``cairn.watcher`` mirror (stable.db file mirror) is gone: the real
Git working tree is the canonical source of truth.  These tests carry the
adversarial properties the mirror era failed:

- §2.4a: ``.gitignore`` is honored (``.env``/``id_rsa``/``secrets.yaml`` are
  never part of the agent's view).
- §2.4b: repo symlinks pointing outside the project are recorded as symlinks
  and never dereferenced (their target content never enters the manifest or
  the disposable workspace).
- §2.5: a fresh capture reflects disk deletions — there is no additive mirror
  that can retain state that disappeared while Cairn was offline.
- §3.3: modes, symlinks, and empty directories are preserved faithfully.
"""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path

import pytest

from cairn.runtime.repo import MaterializeStats, ProjectFilter, _copy_file, capture_manifest, diff_manifests, materialize_workspace


def test_project_filter_honors_gitignore(tmp_path: Path) -> None:
    """Review §2.4a: gitignored files (e.g. .env, id_rsa, secrets.yaml) must
    be excluded from the source set."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / ".gitignore").write_text(".env\nid_rsa\nsecrets.yaml\n", encoding="utf-8")
    for name in (".env", "id_rsa", "secrets.yaml", "ok.txt"):
        (project_root / name).write_text("SECRET", encoding="utf-8")

    f = ProjectFilter(project_root)

    assert f.allows(project_root / ".env") is False
    assert f.allows(project_root / "id_rsa") is False
    assert f.allows(project_root / "secrets.yaml") is False
    # Non-ignored files are still in scope.
    assert f.allows(project_root / "ok.txt") is True


def test_project_filter_never_admits_metadata_or_scaffolding(tmp_path: Path) -> None:
    """VCS metadata, .cairn scaffolding, and .agentfs are never admissible."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    for name in (".git", ".cairn", ".agentfs", ".jj", "__pycache__"):
        (project_root / name).mkdir(parents=True, exist_ok=True)

    f = ProjectFilter(project_root)
    for name in (".git", ".cairn", ".agentfs", ".jj", "__pycache__"):
        assert f.allows(project_root / name) is False, name


def test_capture_never_dereferences_outside_symlinks(tmp_path: Path) -> None:
    """Review §2.4b: a repo symlink pointing outside the project is recorded
    as a symlink entry (target text), and its target content is never read."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "host_secret.txt"
    secret.write_text("HOST_SECRET", encoding="utf-8")
    (project_root / "leak.txt").symlink_to(secret)
    (project_root / "ok.txt").write_text("fine", encoding="utf-8")

    manifest = capture_manifest(project_root)

    leak = manifest.entry_for("leak.txt")
    assert leak is not None
    assert leak.kind == "symlink"
    assert leak.link_target == str(secret)
    assert leak.digest is None  # the target's content was never read
    # The symlink target file is outside the project root: only the link is
    # recorded, nothing about the host file's content.
    assert all(entry.digest is None for entry in manifest.symlinks().values())


def test_fresh_capture_reflects_offline_deletions(tmp_path: Path) -> None:
    """Review §2.5: the snapshot is reconciliatory by construction — a file
    deleted from disk while Cairn was offline does not survive a fresh
    capture (there is no additive mirror to retain stale entries)."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "keep.txt").write_text("keep", encoding="utf-8")
    (project_root / "gone.txt").write_text("gone", encoding="utf-8")

    first = capture_manifest(project_root)
    assert "gone.txt" in first.entries and "keep.txt" in first.entries

    # While Cairn was offline, gone.txt was deleted from disk.
    (project_root / "gone.txt").unlink()
    fresh = capture_manifest(project_root)

    assert "gone.txt" not in fresh.entries
    assert "keep.txt" in fresh.entries


def test_materialize_preserves_modes_symlinks_and_empty_dirs(tmp_path: Path) -> None:
    """Review §3.3: the disposable workspace is a faithful copy — modes,
    symlinks (as symlinks), and empty directories survive."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "src").mkdir(parents=True)
    (project_root / "src" / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (project_root / "src" / "run.sh").chmod(0o755)
    (project_root / "empty_dir").mkdir()
    secret = tmp_path / "host.txt"
    secret.write_text("HOST", encoding="utf-8")
    (project_root / "link").symlink_to(secret)
    (project_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (project_root / "debug.log").write_text("noise", encoding="utf-8")

    dst = tmp_path / "ws"
    materialize_workspace(project_root, dst)

    dst_manifest = capture_manifest(dst)
    assert dst_manifest.entry_for("src/run.sh").mode == 0o755  # type: ignore[union-attr]
    assert dst_manifest.entry_for("src/run.sh").digest == capture_manifest(project_root).files()["src/run.sh"].digest
    link = dst_manifest.entry_for("link")
    assert link is not None and link.kind == "symlink" and link.link_target == str(secret)
    assert dst_manifest.entry_for("empty_dir") is not None
    # Ignored files never reach the disposable workspace.
    assert "debug.log" not in dst_manifest.entries
    assert "host.txt" not in dst_manifest.entries


def test_diff_manifests_tracks_absent_states(tmp_path: Path) -> None:
    """Explicit absent states: a path absent from the base manifest but
    present now is *added*, and vice versa."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "a.txt").write_text("v1", encoding="utf-8")

    base = capture_manifest(project_root)
    (project_root / "a.txt").write_text("v2", encoding="utf-8")
    (project_root / "created.txt").write_text("new", encoding="utf-8")
    (project_root / "gone.txt").write_text("bye", encoding="utf-8")
    base_with_gone = capture_manifest(project_root)
    (project_root / "gone.txt").unlink()

    diff = diff_manifests(base, capture_manifest(project_root))
    assert "a.txt" in diff.modified
    assert "created.txt" in diff.added

    diff2 = diff_manifests(base_with_gone, capture_manifest(project_root))
    assert "gone.txt" in diff2.removed


def test_capture_and_materialize_respect_nested_gitignore(tmp_path: Path) -> None:
    """A nested .gitignore applies to its subtree only."""
    project_root = tmp_path / "project"
    (project_root / "src").mkdir(parents=True)
    (project_root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (project_root / "src" / ".gitignore").write_text("!keep.tmp\n", encoding="utf-8")
    (project_root / "root.tmp").write_text("ignored by root", encoding="utf-8")
    (project_root / "src" / "keep.tmp").write_text("kept by nested negation", encoding="utf-8")
    (project_root / "src" / "drop.tmp").write_text("still ignored", encoding="utf-8")

    manifest = capture_manifest(project_root)
    assert "root.tmp" not in manifest.entries
    assert "src/keep.tmp" in manifest.entries
    assert "src/drop.tmp" not in manifest.entries


def test_directory_named_like_excluded_suffix_is_not_recorded_but_is_descended(tmp_path: Path) -> None:
    """A directory named *.pyc is excluded as an entry (suffix rule) but its
    contents remain part of the tree — the suffix rule is not hereditary."""
    d = tmp_path / "foo.pyc"
    d.mkdir()
    (d / "bar.py").write_text("x = 1", encoding="utf-8")

    manifest = capture_manifest(tmp_path)

    assert "foo.pyc" not in manifest.entries
    assert "foo.pyc/bar.py" in manifest.entries


def test_excluded_directories_are_pruned_not_merely_filtered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """.git and friends are never scanned — not scanned-then-discarded."""
    (tmp_path / "keep.py").write_text("x = 1", encoding="utf-8")
    junk = tmp_path / ".git" / "objects"
    junk.mkdir(parents=True)
    for i in range(50):
        (junk / f"o{i}").write_text("junk", encoding="utf-8")

    scanned: list[str] = []
    real_scandir = os.scandir

    def counting_scandir(path):
        scanned.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", counting_scandir)
    manifest = capture_manifest(tmp_path)

    assert "keep.py" in manifest.entries
    assert not any(".git" in s for s in scanned), "excluded subtree was scanned"


def test_gitignore_negation_under_excluded_dir_matches_git(tmp_path: Path) -> None:
    """git cannot re-include a file under an excluded directory; nor do we."""
    (tmp_path / ".gitignore").write_text("build/\n!build/important.txt\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "important.txt").write_text("keep", encoding="utf-8")

    manifest = capture_manifest(tmp_path)

    assert "build/important.txt" not in manifest.entries


def test_reflink_falls_back_cleanly_when_unsupported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsupported FICLONE must produce a byte-identical plain copy."""
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "not supported")

    monkeypatch.setattr(fcntl, "ioctl", boom)

    src = tmp_path / "a.bin"
    src.write_bytes(os.urandom(1024 * 64))
    dst = tmp_path / "b.bin"
    stats = MaterializeStats()
    _copy_file(src, dst, stats=stats)

    assert dst.read_bytes() == src.read_bytes()
    assert stats.mode == "copy"


def test_materialize_is_byte_identical_either_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reflink and copy paths must be indistinguishable in content."""
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a.bin").write_bytes(os.urandom(1024 * 32))
    (project / "src" / "b.txt").write_text("hello", encoding="utf-8")

    reflink_dst = tmp_path / "ws-reflink"
    copy_dst = tmp_path / "ws-copy"

    materialize_workspace(project, reflink_dst)
    monkeypatch.setattr(
        fcntl,
        "ioctl",
        lambda *args: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, "not supported")),
    )
    materialize_workspace(project, copy_dst)

    def digests(root: Path) -> dict[str, str]:
        manifest = capture_manifest(root)
        return {rel: entry.digest or "" for rel, entry in manifest.files().items()}

    assert digests(copy_dst) == digests(reflink_dst)
