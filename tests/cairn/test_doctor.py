"""Tests for `cairn doctor`.

The point of these is the *negative* cases.  A diagnostic that cannot fail is
worse than no diagnostic, because it reports healthy while nothing is checked
-- which is exactly the failure mode `doctor` exists to catch.  Every check
here is exercised in both directions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cairn.runtime.doctor import (
    REQUIRED_ISOLATION_FLAGS,
    CheckStatus,
    DoctorReport,
    check_bwrap,
    check_closure_manifest,
    check_isolation_effective,
    check_isolation_flags,
    check_materialization,
    check_sandbox_launches,
    check_sandbox_python,
    format_report,
    isolation_breaches,
    run_doctor,
)
from cairn.runtime.settings import ExecutorSettings
from tests.cairn.sandbox_env import requires_sandbox

# ---------------------------------------------------------------------------
# Runtime resolution
# ---------------------------------------------------------------------------


def test_bwrap_missing_fails() -> None:
    result = check_bwrap(ExecutorSettings(bwrap_path="/nonexistent/bwrap"))
    assert result.status is CheckStatus.FAIL
    assert "/nonexistent/bwrap" in result.detail


@requires_sandbox
def test_bwrap_present_reports_version() -> None:
    result = check_bwrap(ExecutorSettings())
    assert result.status is CheckStatus.OK
    assert "bubblewrap" in result.detail.lower() or "bwrap" in result.detail.lower()


def test_sandbox_python_missing_fails() -> None:
    result = check_sandbox_python(ExecutorSettings(python_path="/nonexistent/python"))
    assert result.status is CheckStatus.FAIL
    assert "CAIRN_EXECUTOR_PYTHON_PATH" in result.detail


def test_sandbox_python_present_passes() -> None:
    import sys

    result = check_sandbox_python(ExecutorSettings(python_path=sys.executable))
    assert result.status is CheckStatus.OK


# ---------------------------------------------------------------------------
# Closure manifest
# ---------------------------------------------------------------------------


def test_closure_manifest_unset_skips() -> None:
    result = check_closure_manifest(ExecutorSettings(sandbox_closure_path=None))
    assert result.status is CheckStatus.SKIP


def test_closure_manifest_unreadable_fails_and_names_the_fallback(tmp_path: Path) -> None:
    """An unreadable manifest silently widens the sandbox to all of /nix/store."""
    result = check_closure_manifest(ExecutorSettings(sandbox_closure_path=str(tmp_path / "missing.txt")))
    assert result.status is CheckStatus.FAIL
    assert "/nix/store" in result.detail


def test_closure_manifest_empty_fails(tmp_path: Path) -> None:
    manifest = tmp_path / "closure.txt"
    manifest.write_text("\n  \n", encoding="utf-8")
    result = check_closure_manifest(ExecutorSettings(sandbox_closure_path=str(manifest)))
    assert result.status is CheckStatus.FAIL


def test_closure_manifest_with_missing_store_paths_fails(tmp_path: Path) -> None:
    manifest = tmp_path / "closure.txt"
    manifest.write_text("/nix/store/definitely-not-a-real-store-path\n", encoding="utf-8")
    result = check_closure_manifest(ExecutorSettings(sandbox_closure_path=str(manifest)))
    assert result.status is CheckStatus.FAIL
    assert "missing" in result.detail


def test_closure_manifest_all_present_passes(tmp_path: Path) -> None:
    manifest = tmp_path / "closure.txt"
    manifest.write_text(f"{tmp_path}\n", encoding="utf-8")
    result = check_closure_manifest(ExecutorSettings(sandbox_closure_path=str(manifest)))
    assert result.status is CheckStatus.OK
    assert "1 store paths" in result.detail


# ---------------------------------------------------------------------------
# Isolation flags
# ---------------------------------------------------------------------------


def test_isolation_flags_present_passes() -> None:
    result = check_isolation_flags(ExecutorSettings(bwrap_path="/usr/bin/bwrap", python_path="/usr/bin/python3"))
    assert result.status is CheckStatus.OK


@pytest.mark.parametrize("dropped", REQUIRED_ISOLATION_FLAGS)
def test_isolation_flags_detects_each_dropped_flag(monkeypatch: pytest.MonkeyPatch, dropped: str) -> None:
    """Removing any single isolation flag must fail the check by name."""
    from cairn.runtime.sandbox.sandbox import BwrapExecutor

    original = BwrapExecutor._build_argv
    monkeypatch.setattr(
        BwrapExecutor,
        "_build_argv",
        lambda self: [arg for arg in original(self) if arg != dropped],
    )
    result = check_isolation_flags(ExecutorSettings(bwrap_path="/usr/bin/bwrap", python_path="/usr/bin/python3"))
    assert result.status is CheckStatus.FAIL
    assert dropped in result.detail


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def test_materialization_reports_a_mode(tmp_path: Path) -> None:
    """Same-directory probe: reflink on btrfs/xfs, copy elsewhere. Both fine."""
    result = check_materialization(tmp_path, tmp_path)
    assert result.status in {CheckStatus.OK, CheckStatus.WARN}
    assert result.detail.startswith(("reflink", "full copy"))


def test_materialization_warns_across_filesystems(tmp_path: Path) -> None:
    """/dev/shm is tmpfs, so FICLONE fails EXDEV and the degradation is reported."""
    shm = Path("/dev/shm")
    if not shm.is_dir():
        pytest.skip("/dev/shm unavailable")
    result = check_materialization(tmp_path, shm)
    if result.status is CheckStatus.OK:
        pytest.skip("probe reflinked across the pair; nothing degraded to report")
    assert result.status is CheckStatus.WARN
    assert "full copy" in result.detail
    assert "CAIRN_HOME" in result.detail


def test_materialization_handles_nonexistent_paths(tmp_path: Path) -> None:
    """Runs before `cairn up` has created anything; must not explode."""
    result = check_materialization(tmp_path / "no-project", tmp_path / "no-home")
    assert result.status in {CheckStatus.OK, CheckStatus.WARN}


# ---------------------------------------------------------------------------
# Behavioural isolation (requires a real sandbox)
# ---------------------------------------------------------------------------


@requires_sandbox
def test_sandbox_launches() -> None:
    from tests.cairn.sandbox_env import BWRAP, SANDBOX_PYTHON

    result = check_sandbox_launches(ExecutorSettings(bwrap_path=BWRAP, python_path=SANDBOX_PYTHON))
    assert result.status is CheckStatus.OK


@requires_sandbox
def test_isolation_is_effective_not_merely_configured() -> None:
    """Verified from inside the sandbox: no host home, no network."""
    from tests.cairn.sandbox_env import BWRAP, SANDBOX_PYTHON

    result = check_isolation_effective(ExecutorSettings(bwrap_path=BWRAP, python_path=SANDBOX_PYTHON))
    assert result.status is CheckStatus.OK, result.detail


def test_isolation_breach_detector_discriminates() -> None:
    """The doctor's own detector must fire on unconfined output and stay quiet
    on confined output.  A detector that never fires is decorative."""
    assert isolation_breaches("home=True net=True") == [
        "host home is READABLE",
        "network is REACHABLE",
    ]
    assert isolation_breaches("home=True net=False") == ["host home is READABLE"]
    assert isolation_breaches("home=False net=True") == ["network is REACHABLE"]
    assert isolation_breaches("home=False net=False") == []


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def test_report_exit_code_and_rendering() -> None:
    from cairn.runtime.doctor import CheckResult

    ok = DoctorReport(results=[CheckResult("a", CheckStatus.OK, "fine")])
    assert ok.exit_code == 0 and not ok.failed and not ok.warned

    warned = DoctorReport(results=[CheckResult("a", CheckStatus.WARN, "degraded")])
    assert warned.exit_code == 0 and warned.warned
    assert "degraded" in format_report(warned)

    failed = DoctorReport(results=[CheckResult("a", CheckStatus.FAIL, "broken")])
    assert failed.exit_code == 1 and failed.failed
    assert "FAILED" in format_report(failed)


def test_run_doctor_shallow_skips_sandbox_launch(tmp_path: Path) -> None:
    report = run_doctor(project_root=tmp_path, cairn_home=tmp_path / "home", deep=False)
    names = {result.name for result in report.results}
    assert "sandbox launch" not in names
    assert "isolation flags" in names


@requires_sandbox
def test_run_doctor_deep_is_clean_on_a_working_runtime(tmp_path: Path) -> None:
    from tests.cairn.sandbox_env import BWRAP, SANDBOX_PYTHON

    report = run_doctor(
        project_root=tmp_path,
        cairn_home=tmp_path / "home",
        settings=ExecutorSettings(bwrap_path=BWRAP, python_path=SANDBOX_PYTHON),
        deep=True,
    )
    assert not report.failed, format_report(report)


def test_cli_doctor_subcommand_is_wired() -> None:
    from cairn.cli.cli import build_parser

    args = build_parser().parse_args(["doctor", "--shallow"])
    assert args.command == "doctor"
    assert args.shallow is True
    assert args.is_async is False
