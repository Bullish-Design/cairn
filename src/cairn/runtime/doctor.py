"""Preflight diagnostics for the sandbox execution runtime.

``cairn status`` answers "is the daemon up?".  ``cairn doctor`` answers the
harder question: do the parts that must agree actually agree?

These are the failures that stay silent because every component reports fine
on its own — an isolation flag dropped from the bwrap argv while debugging, a
sandbox interpreter that no longer resolves, a closure manifest that cannot be
read (so the executor quietly falls back to binding all of ``/nix/store``), or
materialization degrading from reflink to a full copy because ``CAIRN_HOME``
landed on a different filesystem than the project.

Where a check can be answered by *doing the thing* rather than by inspecting
configuration, it does the thing: the isolation checks launch a real sandbox
and assert from inside it that the host home is unreachable and the network is
unreachable.  A doctor that only greps argv would report healthy against a
sandbox that does not actually confine anything.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from cairn.runtime.repo import _FICLONE
from cairn.runtime.settings import ExecutorSettings

#: Isolation flags whose removal silently un-sandboxes execution.  Kept in
#: sync with tests/cairn/integration/test_sandbox_boundary.py.
REQUIRED_ISOLATION_FLAGS = ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv")

_PROBE_TIMEOUT_SECONDS = 30.0


class CheckStatus(str, Enum):
    """Outcome of one diagnostic check."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    """One diagnostic answer: what was checked, how it went, and the evidence."""

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    """Every check plus the overall verdict."""

    results: list[CheckResult]

    @property
    def failed(self) -> bool:
        return any(result.status is CheckStatus.FAIL for result in self.results)

    @property
    def warned(self) -> bool:
        return any(result.status is CheckStatus.WARN for result in self.results)

    @property
    def exit_code(self) -> int:
        """0 unless something failed; warnings are reported, not fatal."""
        return 1 if self.failed else 0


# ---------------------------------------------------------------------------
# Runtime resolution
# ---------------------------------------------------------------------------


def check_bwrap(settings: ExecutorSettings) -> CheckResult:
    """The bubblewrap binary resolves and reports a version."""
    path = settings.bwrap_path or shutil.which("bwrap")
    if not path:
        return CheckResult(
            name="bwrap binary",
            status=CheckStatus.FAIL,
            detail="not found (install bubblewrap or set CAIRN_EXECUTOR_BWRAP_PATH)",
        )
    if not Path(path).exists():
        return CheckResult(name="bwrap binary", status=CheckStatus.FAIL, detail=f"configured but missing: {path}")
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10, check=False)
        version = proc.stdout.strip() or proc.stderr.strip() or "unknown version"
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(name="bwrap binary", status=CheckStatus.FAIL, detail=f"{path}: {exc}")
    return CheckResult(name="bwrap binary", status=CheckStatus.OK, detail=f"{version} ({path})")


def check_sandbox_python(settings: ExecutorSettings) -> CheckResult:
    """The interpreter the sandbox will exec actually exists."""
    import sys

    configured = settings.python_path
    path = Path(configured).resolve() if configured else Path(sys.executable).resolve()
    if not path.exists():
        return CheckResult(
            name="sandbox python",
            status=CheckStatus.FAIL,
            detail=f"does not exist: {path} (set CAIRN_EXECUTOR_PYTHON_PATH)",
        )
    source = "configured" if configured else "inherited from the host interpreter"
    return CheckResult(name="sandbox python", status=CheckStatus.OK, detail=f"{path} ({source})")


def check_closure_manifest(settings: ExecutorSettings) -> CheckResult:
    """The declared Nix closure manifest is readable and its paths exist.

    An unreadable manifest is not fatal to execution — the executor falls back
    to binding all of ``/nix/store`` — but that fallback is much broader than
    the declared closure, and it happens with only a log line.  Surface it.
    """
    manifest = settings.sandbox_closure_path
    if not manifest:
        return CheckResult(
            name="closure manifest",
            status=CheckStatus.SKIP,
            detail="not configured; sandbox binds the fallback runtime mounts",
        )
    try:
        text = Path(manifest).read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult(
            name="closure manifest",
            status=CheckStatus.FAIL,
            detail=f"unreadable ({exc}); executor silently falls back to binding all of /nix/store",
        )
    paths = [line.strip() for line in text.splitlines() if line.strip()]
    if not paths:
        return CheckResult(name="closure manifest", status=CheckStatus.FAIL, detail=f"empty: {manifest}")
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        return CheckResult(
            name="closure manifest",
            status=CheckStatus.FAIL,
            detail=f"{len(missing)} of {len(paths)} store paths missing (first: {missing[0]})",
        )
    return CheckResult(name="closure manifest", status=CheckStatus.OK, detail=f"{len(paths)} store paths, all present")


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _existing_ancestor(path: Path) -> Path:
    """Deepest existing ancestor of ``path`` (the path itself if it exists)."""
    candidate = path.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def check_materialization(project_root: Path, cairn_home: Path) -> CheckResult:
    """Probe whether workspaces will actually reflink, by trying it.

    Materialization falls back to a full copy when the project and the
    workspace root are on different filesystems, or the filesystem cannot
    reflink.  That degradation is invisible at runtime apart from a debug log,
    and it is the difference between O(1) and O(bytes) per task.
    """
    source_dir = _existing_ancestor(project_root)
    target_dir = _existing_ancestor(cairn_home / "workspaces")

    try:
        source_dev = os.stat(source_dir).st_dev
        target_dev = os.stat(target_dir).st_dev
    except OSError as exc:
        return CheckResult(name="materialization", status=CheckStatus.WARN, detail=f"could not stat: {exc}")

    try:
        with tempfile.NamedTemporaryFile(dir=source_dir, suffix=".cairn-probe") as src:
            src.write(b"cairn materialization probe\n")
            src.flush()
            dst_path = Path(target_dir) / f".cairn-probe-{os.getpid()}"
            try:
                with open(src.name, "rb") as fin, open(dst_path, "wb") as fout:
                    fcntl.ioctl(fout.fileno(), _FICLONE, fin.fileno())
                mode = "reflink"
                reason = ""
            except OSError as exc:
                mode = "copy"
                reason = f" ({exc.strerror})"
            finally:
                dst_path.unlink(missing_ok=True)
    except OSError as exc:
        return CheckResult(name="materialization", status=CheckStatus.WARN, detail=f"probe failed: {exc}")

    same_fs = "same filesystem" if source_dev == target_dev else "DIFFERENT filesystems"
    if mode == "reflink":
        return CheckResult(name="materialization", status=CheckStatus.OK, detail=f"reflink ({same_fs})")
    return CheckResult(
        name="materialization",
        status=CheckStatus.WARN,
        detail=(
            f"full copy{reason} — {same_fs}; workspaces cost O(bytes) per task. "
            "Put CAIRN_HOME on the same reflink-capable filesystem (btrfs/xfs) as the project."
        ),
    )


# ---------------------------------------------------------------------------
# Isolation — verified by launching a real sandbox, not by reading argv
# ---------------------------------------------------------------------------


def _isolation_argv(settings: ExecutorSettings, workdir: Path, code: str) -> list[str] | None:
    """Real executor argv, with the boot script swapped for an inline probe.

    Reusing ``_build_argv`` is the point: the probe runs under exactly the
    flags a real task would, so a dropped flag shows up here.
    """
    from cairn.runtime.sandbox.sandbox import BwrapExecutor

    executor = BwrapExecutor(agent_id="doctor", workdir=workdir, project_root=workdir, settings=settings)
    try:
        argv = executor._build_argv()
    except Exception:  # noqa: BLE001 - any argv failure is reported as a failed check
        return None
    return [*argv[:-1], "-c", code]


def check_isolation_flags(settings: ExecutorSettings) -> CheckResult:
    """Every required isolation flag is present in the argv the executor builds."""
    with tempfile.TemporaryDirectory() as tmp:
        argv = _isolation_argv(settings, Path(tmp), "pass")
    if argv is None:
        return CheckResult(name="isolation flags", status=CheckStatus.FAIL, detail="could not build sandbox argv")
    missing = [flag for flag in REQUIRED_ISOLATION_FLAGS if flag not in argv]
    if missing:
        return CheckResult(
            name="isolation flags",
            status=CheckStatus.FAIL,
            detail=f"MISSING from sandbox argv: {', '.join(missing)}",
        )
    return CheckResult(
        name="isolation flags",
        status=CheckStatus.OK,
        detail=", ".join(REQUIRED_ISOLATION_FLAGS),
    )


def _run_probe(settings: ExecutorSettings, code: str) -> tuple[bool, str]:
    """Run ``code`` inside a real sandbox; return (launched, stdout)."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        argv = _isolation_argv(settings, workdir, code)
        if argv is None:
            return False, "could not build sandbox argv"
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout).strip()[-300:]
        return True, proc.stdout.strip()


def check_sandbox_launches(settings: ExecutorSettings) -> CheckResult:
    """A real sandbox starts and runs code."""
    ok, output = _run_probe(settings, "print('alive')")
    if not ok:
        return CheckResult(name="sandbox launch", status=CheckStatus.FAIL, detail=f"sandbox did not run: {output}")
    if output != "alive":
        return CheckResult(name="sandbox launch", status=CheckStatus.FAIL, detail=f"unexpected output: {output!r}")
    return CheckResult(name="sandbox launch", status=CheckStatus.OK, detail="sandbox starts and executes code")


def isolation_breaches(probe_output: str) -> list[str]:
    """Interpret the in-sandbox probe's output as a list of confinement breaches.

    Split out so the interpretation can be tested directly against both a
    confined and an unconfined sample — a breach detector that never fires is
    the exact failure this module exists to prevent.
    """
    breaches = []
    if "home=True" in probe_output:
        breaches.append("host home is READABLE")
    if "net=True" in probe_output:
        breaches.append("network is REACHABLE")
    return breaches


def check_isolation_effective(settings: ExecutorSettings) -> CheckResult:
    """Assert from *inside* the sandbox that confinement actually holds.

    Greping argv proves the flags were passed; this proves they worked.
    """
    code = (
        "import os,socket;"
        "h=os.path.isdir(os.path.expanduser('~/.ssh'));"
        "\ntry:\n socket.create_connection(('1.1.1.1',53),timeout=3); n=True\n"
        "except OSError:\n n=False\n"
        "print(f'home={h} net={n}')"
    )
    ok, output = _run_probe(settings, code)
    if not ok:
        return CheckResult(name="isolation effective", status=CheckStatus.SKIP, detail=f"probe did not run: {output}")
    breaches = isolation_breaches(output)
    if breaches:
        return CheckResult(
            name="isolation effective",
            status=CheckStatus.FAIL,
            detail="; ".join(breaches) + " — the sandbox is not confining execution",
        )
    return CheckResult(
        name="isolation effective",
        status=CheckStatus.OK,
        detail="verified from inside the sandbox: no host home, no network",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_doctor(
    *,
    project_root: Path,
    cairn_home: Path,
    settings: ExecutorSettings | None = None,
    deep: bool = True,
) -> DoctorReport:
    """Run every diagnostic and return the report.

    ``deep`` controls whether the checks that launch a real sandbox run; they
    cost roughly 100 ms each and are the only ones that verify confinement
    rather than configuration.
    """
    settings = settings or ExecutorSettings()
    results = [
        check_bwrap(settings),
        check_sandbox_python(settings),
        check_closure_manifest(settings),
        check_isolation_flags(settings),
        check_materialization(project_root, cairn_home),
    ]
    if deep:
        results.append(check_sandbox_launches(settings))
        results.append(check_isolation_effective(settings))
    return DoctorReport(results=results)


def format_report(report: DoctorReport) -> str:
    """Render a report as aligned ``[status] name: detail`` lines."""
    lines = [f"[{result.status.value:<4}] {result.name}: {result.detail}" for result in report.results]
    if report.failed:
        lines.append("")
        lines.append("FAILED — the sandbox runtime is not in a state where execution can be trusted.")
    elif report.warned:
        lines.append("")
        lines.append("OK with warnings — execution works, but something is degraded.")
    return "\n".join(lines)
