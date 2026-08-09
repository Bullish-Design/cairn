"""Run the Cairn walkthrough and emit ``demo/out/WALKTHROUGH.md``.

Runner order (guide §P1): env guard → ``cairn doctor`` (deep), abort on
failure with setup guidance → build the fixture → run chapters → write the
transcript.

Isolation (guide §2.3) is non-negotiable: this demo drives agents against a
throwaway fixture, never the user's checkout.  Two independent guards:

1. **Env guard** — ``PathsSettings`` lets ``CAIRN_PATHS_*`` override
   constructor args, so the demo aborts if either var is set.
2. **Post-condition guard** — every act that builds an orchestrator asserts
   ``orch.project_root == fixture root`` before spawning anything.

Layout: scratch state (fixture project, cairn homes, doctor probes) always
lives under ``demo/out/``; ``--out`` names the transcript file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from demo import fixture
from demo.narrator import Narrator
from demo.runner_ctx import ChapterContext, DemoOptions

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "demo" / "out"
DEFAULT_TRANSCRIPT = DEFAULT_OUT_DIR / "WALKTHROUGH.md"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m demo", description="Run the Cairn walkthrough")
    parser.add_argument("--only", help="Run a single chapter by id (e.g. 07)")
    parser.add_argument("--act", help="Run a single act by numeral (0-4)")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep demo/out/ scratch state after the run (default: removed on success)",
    )
    parser.add_argument(
        "--no-daemon",
        action="store_true",
        help="Skip Act IV (daemon & thin client); used by CI for stability",
    )
    parser.add_argument(
        "--include-recovery",
        action="store_true",
        help="Include ch20 (recovery after a daemon death); off by default, skips loudly",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_TRANSCRIPT,
        help="Transcript file path (default: demo/out/WALKTHROUGH.md)",
    )
    return parser


def _env_guard() -> None:
    """Abort if the user has CAIRN_PATHS_* set: those override constructor
    args, so the demo could silently operate on the wrong tree."""
    offenders = [name for name in os.environ if name.startswith("CAIRN_PATHS_")]
    if offenders:
        raise SystemExit(
            "Refusing to run the demo: CAIRN_PATHS_* env vars are set "
            f"({', '.join(sorted(offenders))}). The demo manages its own "
            "project_root and cairn_home under demo/out/ and must not inherit "
            "yours — unset them and re-run."
        )


def _run_doctor(out_dir: Path) -> None:
    """Deep doctor; abort on failure with setup guidance (never a mocked
    executor — a demo that skips the sandbox check would print fake green)."""
    from cairn.runtime.doctor import format_report, run_doctor
    from cairn.runtime.settings import ExecutorSettings

    probe_project = out_dir / "doctor-project"
    probe_home = out_dir / "doctor-home"
    probe_project.mkdir(parents=True, exist_ok=True)

    report = run_doctor(
        project_root=probe_project,
        cairn_home=probe_home,
        settings=ExecutorSettings(),
        deep=True,
    )
    text = format_report(report)
    if report.failed:
        raise SystemExit(
            f"cairn doctor failed — the sandbox runtime is not trustworthy, refusing to run the demo.\n\n{text}\n\n"
            "Setup guidance: run inside `devenv shell` (sets CAIRN_EXECUTOR_*), or install bubblewrap and set\n"
            "  CAIRN_EXECUTOR_BWRAP_PATH=<bwrap binary>\n"
            "  CAIRN_EXECUTOR_PYTHON_PATH=<stdlib-only python3>\n"
        )
    print(text)
    print("doctor: OK — continuing with a real sandbox")


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    options = DemoOptions(
        out_dir=DEFAULT_OUT_DIR,
        transcript_path=args.out.resolve(),
        keep=args.keep,
        no_daemon=args.no_daemon,
        include_recovery=args.include_recovery,
        only=args.only,
        act=args.act,
    )

    _env_guard()
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    _run_doctor(DEFAULT_OUT_DIR)

    from demo.chapters import run_selected

    fixture_root = fixture.build(options.project_root)
    print(f"fixture built at {fixture_root}")

    narrator = Narrator(options.transcript_path)
    try:
        run_selected(narrator, options, fixture_root)
    finally:
        narrator.close()

    print(f"\nwalkthrough written to {options.transcript_path}")

    if not options.keep:
        # Retain the transcript; drop the scratch state (fixture, homes, probes).
        for name in ChapterContext.SCRATCH_DIRS:
            shutil.rmtree(DEFAULT_OUT_DIR / name, ignore_errors=True)
        print("demo/out/ scratch removed (pass --keep to retain the fixture and homes for inspection)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
