"""bwrap-backed sandbox executor for agent code.

The executor runs agent code as a stock CPython process inside a bubblewrap
sandbox. The workflow is:

1. **Materialize** — the agent overlay (over stable) is written to a real
   directory (``$CAIRN_HOME/workspaces/{agent_id}``) via fsdantic's
   ``materialize.to_disk``, giving the sandbox a real POSIX filesystem view.
2. **Run** — ``bwrap`` launches the sandbox with only that directory bound
   (writable), the interpreter runtime bound (read-only), and everything else
   unshared (no network, no host filesystem, no other processes). On
   NixOS/devenv the runtime is bound from a declarative store-closure manifest
   (``pkgs.writeClosure``), so no dependency discovery happens at runtime.
3. **Re-import** — the changeset written by the sandbox (files added/changed/
   deleted) is diffed against a pre-run manifest and written back into the
   agent overlay, restoring fsdantic as the source of truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from fsdantic import Workspace

from cairn.core.exceptions import CairnError
from cairn.core.exceptions import TimeoutError as CairnTimeoutError
from cairn.core.types import SubmissionData
from cairn.runtime.sandbox import boot as _boot_module
from cairn.runtime.settings import ExecutorSettings

logger = logging.getLogger(__name__)

SANDBOX_DIR_NAME = ".cairn"
SANDBOX_MOUNT_POINT = "/workspace"
BOOT_SCRIPT_RELPATH = f"{SANDBOX_DIR_NAME}/boot.py"
SUBMISSION_RELPATH = f"{SANDBOX_DIR_NAME}/submission.json"

# Fallback read-only runtime mounts (used when no closure manifest is
# configured). Each entry is bound with ``--ro-bind-try`` so missing paths on a
# given distro are ignored.
DEFAULT_RUNTIME_MOUNTS: tuple[tuple[str, str], ...] = (
    ("/usr", "/usr"),
    ("/usr/local", "/usr/local"),
    ("/lib", "/lib"),
    ("/lib64", "/lib64"),
    ("/bin", "/bin"),
    ("/etc/ld.so.cache", "/etc/ld.so.cache"),
)

# Common devices provided by bwrap --dev are enough for the stdlib; no raw
# device access is granted.
SANDBOX_UID = 65534  # nobody
SANDBOX_GID = 65534  # nobody


class SandboxExecutionError(CairnError):
    """Sandboxed execution failed (nonzero exit, missing runtime, launch failure)."""


@dataclass
class SandboxResult:
    """Outcome of a sandboxed execution."""

    submission: SubmissionData | None
    changes: dict[str, list[str]] = field(default_factory=lambda: {"written": [], "deleted": []})
    log: str = ""


class BwrapExecutor:
    """Run agent code inside a bubblewrap sandbox over a materialized workspace."""

    def __init__(
        self,
        *,
        agent_id: str,
        workdir: Path | str,
        agent_fs: Workspace,
        stable: Workspace,
        settings: ExecutorSettings,
        allow_root: Path | str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.workdir = Path(workdir)
        self.agent_fs = agent_fs
        self.stable = stable
        self.settings = settings
        self.allow_root = Path(allow_root) if allow_root is not None else self.workdir.parent

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, *, code: str, task: str) -> SandboxResult:
        """Materialize the workspace, run ``code`` in the sandbox, re-import changes.

        Raises:
            SandboxExecutionError: If the sandbox cannot launch or the code exits
                with a nonzero status.
            CairnTimeoutError: If execution exceeds ``settings.max_execution_time``.
        """
        workdir = self.workdir
        workdir.mkdir(parents=True, exist_ok=True)

        # Materialize the merged overlay-on-stable view into the workdir.
        await self.agent_fs.materialize.to_disk(
            target_path=workdir,
            base=self.stable,
            clean=True,
            allow_root=self.allow_root,
        )

        cairn_dir = workdir / SANDBOX_DIR_NAME
        cairn_dir.mkdir(parents=True, exist_ok=True)
        (cairn_dir / "task.py").write_text(code, encoding="utf-8")
        (cairn_dir / "task.json").write_text(
            json.dumps({"task_description": task}, indent=2),
            encoding="utf-8",
        )
        boot_source = Path(_boot_module.__file__).read_text(encoding="utf-8")
        (cairn_dir / "boot.py").write_text(boot_source, encoding="utf-8")

        baseline = self._snapshot(workdir)
        argv = self._build_argv()

        proc = await self._spawn(argv)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.settings.max_execution_time,
            )
        except TimeoutError:
            proc.kill()
            with _suppress_timeout():
                await proc.communicate()
            raise CairnTimeoutError(
                f"Operation exceeded timeout of {self.settings.max_execution_time}s",
                error_code="EXECUTION_TIMEOUT",
                context={"agent_id": self.agent_id, "timeout_seconds": self.settings.max_execution_time},
            ) from None

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        run_log = f"{stdout_text}\n{stderr_text}".strip()
        (cairn_dir / "run.log").write_text(run_log, encoding="utf-8")

        if proc.returncode != 0:
            raise SandboxExecutionError(
                f"Sandbox execution failed (exit {proc.returncode}): {self._format_error(stderr_text)}",
                error_code="SANDBOX_EXECUTION_FAILED",
                context={
                    "agent_id": self.agent_id,
                    "exit_code": proc.returncode,
                    "stderr": stderr_text[-4000:],
                },
            )

        written, deleted = self._diff_snapshot(workdir, baseline)
        await self._reimport(written, deleted)

        submission = self._read_submission(workdir / SUBMISSION_RELPATH, default_summary=task)
        return SandboxResult(
            submission=submission,
            changes={"written": [rel for rel, _ in written], "deleted": deleted},
            log=run_log,
        )

    # ------------------------------------------------------------------
    # Sandbox invocation
    # ------------------------------------------------------------------

    def _bwrap_path(self) -> str:
        configured = self.settings.bwrap_path
        if configured:
            return configured
        found = shutil.which("bwrap")
        if found is None:
            raise SandboxExecutionError(
                "bwrap binary not found (install bubblewrap or set CAIRN_EXECUTOR_BWRAP_PATH)",
                error_code="SANDBOX_BWRAP_MISSING",
            )
        return found

    def _python_path(self) -> Path:
        configured = self.settings.python_path
        if configured:
            return Path(configured).resolve()
        return Path(sys.executable).resolve()

    def _build_argv(self) -> list[str]:
        bwrap = self._bwrap_path()
        uid = self.settings.sandbox_uid
        gid = self.settings.sandbox_gid
        python = str(self._python_path())

        argv = [
            bwrap,
            "--unshare-all",
            "--die-with-parent",
            "--clearenv",
            "--uid",
            str(uid),
            "--gid",
            str(gid),
        ]
        argv += self._runtime_bind_args()
        argv += [
            "--bind",
            str(self.workdir),
            SANDBOX_MOUNT_POINT,
            "--tmpfs",
            "/tmp",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--setenv",
            "CAIRN_WORKSPACE",
            SANDBOX_MOUNT_POINT,
            "--setenv",
            "CAIRN_MAX_MEMORY_BYTES",
            str(self.settings.max_memory_bytes),
            "--setenv",
            "CAIRN_MAX_CPU_SECONDS",
            str(self.settings.max_execution_time),
            "--setenv",
            "CAIRN_MAX_RECURSION_DEPTH",
            str(self.settings.max_recursion_depth),
            "--setenv",
            "PYTHONUNBUFFERED",
            "1",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
        ]
        argv += [
            python,
            f"{SANDBOX_MOUNT_POINT}/{BOOT_SCRIPT_RELPATH}",
        ]
        return argv

    def _runtime_bind_args(self) -> list[str]:
        """Read-only binds for the interpreter runtime.

        Primary path (NixOS/devenv): bind exactly the store closure declared in
        ``settings.sandbox_closure_path`` (a manifest file produced by
        ``pkgs.writeClosure``). No runtime discovery — the closure is known at
        environment-build time.

        Fallback (no manifest configured): bind the immutable ``/nix/store``,
        the interpreter's standalone prefix if present, and the conventional
        system runtime directories via ``--ro-bind-try``.
        """
        closure = self._closure_paths()
        if closure:
            return [arg for path in closure for arg in ("--ro-bind", path, path)]

        binds = ["--ro-bind-try", "/nix/store", "/nix/store"]
        prefix = self._python_path().parent.parent
        if (prefix / "lib").is_dir():
            binds += ["--ro-bind", str(prefix), str(prefix)]
        for src, dst in (self.settings.runtime_mounts or DEFAULT_RUNTIME_MOUNTS):
            binds += ["--ro-bind-try", src, dst]
        return binds

    def _closure_paths(self) -> list[str]:
        """Read the declared sandbox closure manifest (one store path per line)."""
        manifest = self.settings.sandbox_closure_path
        if not manifest:
            return []
        try:
            text = Path(manifest).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Could not read sandbox closure manifest; falling back to /nix/store bind",
                extra={"manifest": manifest, "error": str(exc)},
            )
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    async def _spawn(self, argv: list[str]) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise SandboxExecutionError(
                f"bwrap not found: {argv[0]} (install bubblewrap or set CAIRN_EXECUTOR_BWRAP_PATH)",
                error_code="SANDBOX_BWRAP_MISSING",
            ) from exc
        except OSError as exc:
            raise SandboxExecutionError(
                f"Failed to launch sandbox: {exc}",
                error_code="SANDBOX_LAUNCH_FAILED",
            ) from exc

    @staticmethod
    def _format_error(stderr: str) -> str:
        """Extract a compact human-readable error from sandbox stderr."""
        lines = [line for line in stderr.splitlines() if line.strip()]
        if not lines:
            return "no error output"
        # Prefer the final traceback line (e.g. "RuntimeError: boom").
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith(("Error:", "Exception:", "ValueError:", "TypeError:", "RuntimeError:", "OSError:")):
                return stripped
        return lines[-1][-500:]

    # ------------------------------------------------------------------
    # Change tracking and re-import
    # ------------------------------------------------------------------

    @classmethod
    def _snapshot(cls, root: Path) -> dict[str, str]:
        """Walk ``root`` returning {relative_path: sha256} for regular files.

        The sandbox scaffolding directory (``.cairn``) is excluded: it is owned
        by the host and must never be re-imported into the overlay.
        """
        manifest: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel == SANDBOX_DIR_NAME or rel.startswith(f"{SANDBOX_DIR_NAME}/"):
                continue
            manifest[rel] = cls._sha256(path)
        return manifest

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _diff_snapshot(cls, root: Path, baseline: dict[str, str]) -> tuple[list[tuple[str, bytes]], list[str]]:
        """Compare the post-run snapshot against the baseline.

        Returns ``(written, deleted)`` where ``written`` is a list of
        ``(relative_path, content)`` for added/changed regular files and
        ``deleted`` is a list of relative paths present before the run but
        missing (or replaced by a symlink) afterwards.
        """
        current = cls._snapshot(root)
        written: list[tuple[str, bytes]] = []
        for rel, digest in current.items():
            if baseline.get(rel) != digest:
                target = root / rel
                if target.is_symlink():  # never follow symlinks on the host side
                    continue
                written.append((rel, target.read_bytes()))
        deleted = [rel for rel in baseline if rel not in current]
        return written, deleted

    async def _reimport(self, written: list[tuple[str, bytes]], deleted: list[str]) -> None:
        """Write sandbox changes back into the agent overlay.

        Writes are sequential (with one retry on transient lock errors) to
        avoid SQLite "database is locked" contention on the single connection.
        """
        for rel, content in written:
            try:
                await self.agent_fs.files.write(rel, content)
            except Exception as exc:  # noqa: BLE001 — retry transient locks once
                await asyncio.sleep(0.05)
                try:
                    await self.agent_fs.files.write(rel, content)
                except Exception as retry_exc:  # noqa: BLE001 — best effort
                    logger.warning(
                        "Failed to re-import sandbox change",
                        extra={"agent_id": self.agent_id, "path": rel, "error": str(exc), "retry_error": str(retry_exc)},
                    )
        for rel in deleted:
            # Only overlay-owned files can be removed (the current fsdantic
            # overlay API has no tombstone mechanism for stable-only files).
            try:
                await self.agent_fs.files.remove(rel)
            except Exception as exc:  # noqa: BLE001 — best-effort tombstone
                logger.warning(
                    "Failed to record sandbox deletion (stable-only files cannot be tombstoned)",
                    extra={"agent_id": self.agent_id, "path": rel, "error": str(exc)},
                )

    @staticmethod
    def _read_submission(path: Path, default_summary: str) -> SubmissionData | None:
        """Parse ``.cairn/submission.json`` written by the sandbox."""
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            summary = default_summary
        changed = payload.get("changed_files")
        if not isinstance(changed, list):
            changed = []
        changed_files = [str(item) for item in changed if isinstance(item, str)]
        submitted_at = payload.get("submitted_at")
        if not isinstance(submitted_at, (int, float)):
            submitted_at = time.time()
        return {
            "summary": summary,
            "changed_files": changed_files,
            "submitted_at": float(submitted_at),
        }


def _suppress_timeout() -> contextlib.AbstractContextManager[None]:
    """Context manager swallowing asyncio.TimeoutError (for post-kill cleanup)."""
    return contextlib.suppress(asyncio.TimeoutError)
