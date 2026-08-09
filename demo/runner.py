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


def _clean_scratch() -> None:
    """Remove the previous run's scratch state so the fixture always starts
    from a pristine tree (accepts from a previous run must not leak in)."""
    for name in ChapterContext.SCRATCH_DIRS:
        shutil.rmtree(DEFAULT_OUT_DIR / name, ignore_errors=True)


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
    _clean_scratch()
    _run_doctor(DEFAULT_OUT_DIR)

    from demo.chapters import run_selected

    fixture_root = fixture.build(options.project_root)
    print(f"fixture built at {fixture_root}")

    narrator = Narrator(options.transcript_path)
    try:
        _write_preamble(narrator, options)
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


def _write_preamble(narrator: Narrator, options: DemoOptions) -> None:
    """Header block: what ran, in which sandbox mode (guide §7 — the
    transcript records closure vs fallback so a degraded sandbox is visible
    in the committed sample)."""
    closure = os.environ.get("CAIRN_EXECUTOR_SANDBOX_CLOSURE_PATH")
    sandbox_mode = (
        f"declared closure manifest ({closure})" if closure else "fallback runtime mounts (no closure manifest)"
    )
    python = os.environ.get("CAIRN_EXECUTOR_PYTHON_PATH") or sys.executable
    bwrap = os.environ.get("CAIRN_EXECUTOR_BWRAP_PATH") or shutil.which("bwrap") or "(discovered)"
    narrator.say(
        f"""
        This walkthrough was generated by ``python -m demo`` — every block
        below is a real capture from the run that produced it.  Nothing here
        touched a real checkout: the fixture, cairn homes, and workspaces
        live only under ``demo/out/``.

        **Run environment** — bubblewrap ``{bwrap}``; sandbox interpreter
        ``{python}``; sandbox mode: **{sandbox_mode}**.
        """
    )


if __name__ == "__main__":
    raise SystemExit(main())
