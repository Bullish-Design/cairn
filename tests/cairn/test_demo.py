"""Fast unit tests for the demo walkthrough machinery.

These never launch a sandbox (the CI job runs the full ``python -m demo``
separately); they pin the guards that make the demo safe to run at all —
the env guard, the fixture's manifest semantics, the narrator's assert-on-
false contract, and the flag surface.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from demo import fixture
from demo.narrator import DemoAssertionError, Narrator
from demo.runner import _build_arg_parser, _env_guard
from demo.runner_ctx import ChapterContext, DemoOptions


def _options(out: Path) -> DemoOptions:
    return DemoOptions(out_dir=out, transcript_path=out / "WALKTHROUGH.md", keep=True)


def test_env_guard_aborts_on_cairn_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAIRN_PATHS_PROJECT_ROOT", "/somewhere")
    with pytest.raises(SystemExit, match="CAIRN_PATHS_"):
        _env_guard()
    monkeypatch.delenv("CAIRN_PATHS_PROJECT_ROOT")
    _env_guard()  # no raise


def test_env_guard_passes_without_cairn_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("CAIRN_PATHS_"):
            monkeypatch.delenv(name)
    _env_guard()


def test_fixture_manifest_semantics(tmp_path: Path) -> None:
    from cairn.runtime.repo import capture_manifest

    root = fixture.build(tmp_path / "project")
    manifest = capture_manifest(root)

    assert "secrets.env" not in manifest.entries  # gitignore admission
    assert "src/doomed.py" in manifest.entries
    link = manifest.entry_for("link_to_main")
    assert link is not None and link.kind == "symlink" and link.link_target == "src/main.py"
    empty = manifest.entry_for("empty_dir")
    assert empty is not None and empty.kind == "dir"
    run_sh = manifest.entry_for("run.sh")
    assert run_sh is not None and (run_sh.mode or 0) & 0o111


def test_tree_digest_changes_with_content(tmp_path: Path) -> None:
    root = fixture.build(tmp_path / "project")
    before = fixture.tree_digest(root)
    (root / "src" / "main.py").write_text('print("changed")\n', encoding="utf-8")
    after = fixture.tree_digest(root)
    assert before != after
    # Gitignored writes do not move the digest.
    (root / "secrets.env").write_text("SECRET_TOKEN=other\n", encoding="utf-8")
    assert fixture.tree_digest(root) == after


def test_narrator_prove_records_and_raises(tmp_path: Path) -> None:
    import io

    transcript = tmp_path / "WALKTHROUGH.md"
    narrator = Narrator(transcript, tee=io.StringIO())
    narrator.prove("true claim", True)
    with pytest.raises(DemoAssertionError):
        narrator.prove("false claim", False)
    narrator.close()

    text = transcript.read_text(encoding="utf-8")
    assert "[ok] true claim" in text
    assert "[FAIL] false claim" in text
    assert narrator.assertion_count == 2


def test_narrator_capture_and_shell_roundtrip(tmp_path: Path) -> None:
    import io
    import sys

    transcript = tmp_path / "WALKTHROUGH.md"
    narrator = Narrator(transcript, tee=io.StringIO())
    narrator.say("hello world")
    narrator.code("print(1)", lang="python")
    narrator.capture("label", "captured text")
    out = narrator.shell([sys.executable, "-c", "print('shelled')"])
    assert out == "shelled"
    narrator.close()

    text = transcript.read_text(encoding="utf-8")
    assert "hello world" in text
    assert "```python" in text
    assert "label\ncaptured text" in text
    assert "shelled" in text


def test_arg_parser_documented_flags() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(["--no-daemon", "--include-recovery", "--keep", "--only", "07", "--out", "/tmp/x.md"])
    assert args.no_daemon is True
    assert args.include_recovery is True
    assert args.keep is True
    assert args.only == "07"
    assert args.out == Path("/tmp/x.md")


def test_chapter_registry_covers_00_to_20() -> None:
    from demo.chapters import ACT_MODULES, run_selected  # noqa: F401 - import surface

    cids: set[str] = set()
    for numeral, module in ACT_MODULES.items():
        for cid, _title, _fn in module.CHAPTERS:
            cids.add(cid)
            assert module.ACT_NUMERAL == numeral
    assert cids == {f"{i:02d}" for i in range(21)}, sorted(cids)


def test_post_condition_guard_binds_fixture(tmp_path: Path) -> None:
    """make_orchestrator must refuse to bind anything but the fixture."""

    out = tmp_path / "out"
    out.mkdir()
    root = fixture.build(out / "project")
    ctx = ChapterContext(
        options=_options(out),
        project_root=root,
        home=out / "home",
        act4_home=out / "act4-home",
    )
    orch = ctx.make_orchestrator()
    assert orch.project_root == root.resolve()
    assert orch.project_root != Path.cwd().resolve()
