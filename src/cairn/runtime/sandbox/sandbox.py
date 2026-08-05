"""bwrap-backed sandbox executor for agent code.

The executor runs agent code as a stock CPython process inside a bubblewrap
sandbox.  The real Git working tree is the canonical source of truth; the
workflow is:

1. **Snapshot** — the project tree is captured faithfully (existence, kind,
   digest, mode, symlink target; gitignore-aware, no symlinks followed).
2. **Materialize** — a disposable real directory (``$CAIRN_HOME/workspaces/
   {agent_id}``) is created as a copy-on-write/reflink copy of the project
   tree, giving the sandbox a faithful POSIX view of the repo.
3. **Run** — ``bwrap`` launches the sandbox with only that directory bound
   (writable), the interpreter runtime bound (read-only), and everything else
   unshared (no network, no host filesystem, no other processes).  On
   NixOS/devenv the runtime is bound from a declarative store-closure
   manifest (``pkgs.writeClosure``), so no dependency discovery happens at
   runtime.
4. **Diff** — the post-run workspace is captured again and compared against
   the base manifest: the computed changeset (written/deleted/mode-changed)
   is the authoritative record of what the agent did.  The agent's submission
   prose is advisory; the diff is truth.

There is no overlay database and no re-import: the changeset lives on disk in
the disposable workspace until the human accepts (apply) or rejects (remove).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from cairn.core.exceptions import CairnError, ResourceLimitError
from cairn.core.exceptions import TimeoutError as CairnTimeoutError
from cairn.core.types import SubmissionData
from cairn.runtime import repo
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


class SandboxExecutionError(CairnError):
    """Sandboxed execution failed (nonzero exit, missing runtime, launch failure)."""


@dataclass
class SandboxResult:
    """Outcome of a sandboxed execution."""

    submission: SubmissionData | None
    changes: dict[str, list[str]] = field(default_factory=lambda: {"written": [], "deleted": []})
    log: str = ""
    base_hashes: dict[str, str] = field(default_factory=dict)  # for the accept staleness check
    base_manifest: dict[str, repo.ManifestEntry] = field(default_factory=dict)  # full fidelity base entries
    exit_code: int = 0
    executable: list[str] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    mode_changed: list[str] = field(default_factory=list)


class BwrapExecutor:
    """Run agent code inside a bubblewrap sandbox over a disposable real workspace."""

    def __init__(
        self,
        *,
        agent_id: str,
        workdir: Path | str,
        project_root: Path | str,
        settings: ExecutorSettings,
    ) -> None:
        self.agent_id = agent_id
        self.workdir = Path(workdir)
        self.project_root = Path(project_root).resolve()
        self.settings = settings

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, *, code: str, task: str) -> SandboxResult:
        """Snapshot the project, materialize a disposable workspace, run
        ``code`` in the sandbox, and return the authoritative changeset.

        Raises:
            SandboxExecutionError: If the sandbox cannot launch or the code exits
                with a nonzero status.
            CairnTimeoutError: If execution exceeds ``settings.max_execution_time``.
            ResourceLimitError: If the workspace budget is exceeded.
        """
        base_manifest = await asyncio.to_thread(self._capture_project)
        await asyncio.to_thread(self._materialize, base_manifest)

        cairn_dir = self.workdir / SANDBOX_DIR_NAME
        cairn_dir.mkdir(parents=True, exist_ok=True)
        (cairn_dir / "task.py").write_text(code, encoding="utf-8")
        (cairn_dir / "task.json").write_text(
            json.dumps({"task_description": task}, indent=2),
            encoding="utf-8",
        )
        boot_source = Path(_boot_module.__file__).read_text(encoding="utf-8")
        (cairn_dir / "boot.py").write_text(boot_source, encoding="utf-8")

        argv = self._build_argv()
        proc = await self._spawn(argv)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.settings.max_execution_time,
            )
        except TimeoutError:
            proc.kill()
            stdout, stderr = b"", b""
            with _suppress_timeout():
                stdout, stderr = await proc.communicate()
            partial = f"{stdout.decode('utf-8', 'replace')}\n{stderr.decode('utf-8', 'replace')}".strip()
            (cairn_dir / "run.log").write_text(
                partial + f"\n\n[cairn] killed after {self.settings.max_execution_time}s\n",
                encoding="utf-8",
            )
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

        current_manifest = await asyncio.to_thread(self._capture_workspace)
        diff = repo.diff_manifests(base_manifest, current_manifest)

        written = diff.written
        deleted = diff.removed
        total_bytes = 0
        for rel in written:
            entry = current_manifest.entry_for(rel)
            if entry is not None and entry.size is not None:
                total_bytes += entry.size
        if total_bytes > self.settings.max_workspace_bytes:
            raise ResourceLimitError(
                f"Sandbox wrote {total_bytes} bytes, exceeding the {self.settings.max_workspace_bytes} byte workspace budget",
                error_code="WORKSPACE_BUDGET_EXCEEDED",
                context={"agent_id": self.agent_id, "bytes_written": total_bytes},
            )
        touched = set(written) | set(deleted)
        base_hashes = {
            rel: entry.digest for rel, entry in base_manifest.files().items() if rel in touched and entry.digest
        }
        # Full-fidelity base entries for every touched path (kind/mode/digest/
        # symlink target); touched paths absent from the base manifest were
        # explicitly absent at run start.
        base_manifest_entries = {rel: entry for rel, entry in base_manifest.entries.items() if rel in touched}
        executable = self._collect_executable(current_manifest, set(written) | set(diff.mode_changed))
        directories = self._collect_empty_dirs(current_manifest)

        submission = self._read_submission(self.workdir / SUBMISSION_RELPATH, default_summary=task)
        return SandboxResult(
            submission=submission,
            changes={"written": written, "deleted": deleted},
            log=run_log,
            base_hashes=base_hashes,
            base_manifest=base_manifest_entries,
            exit_code=proc.returncode or 0,
            executable=executable,
            directories=directories,
            mode_changed=diff.mode_changed,
        )

    # ------------------------------------------------------------------
    # Snapshot + materialization
    # ------------------------------------------------------------------

    def _capture_project(self) -> repo.Manifest:
        return repo.capture_manifest(self.project_root)

    def _materialize(self, base_manifest: repo.Manifest) -> None:
        """(Re)create the disposable workspace from the base manifest state."""
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        repo.materialize_workspace(self.project_root, self.workdir)

    def _capture_workspace(self) -> repo.Manifest:
        return repo.capture_manifest(self.workdir)

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
            "--new-session",  # detach from the controlling terminal
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
            "CAIRN_MAX_OUTPUT_FILE_BYTES",
            str(self.settings.max_output_file_bytes),
            "--setenv",
            "CAIRN_MAX_PROCESSES",
            str(self.settings.max_processes),
            "--setenv",
            "CAIRN_MAX_OPEN_FILES",
            str(self.settings.max_open_files),
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
        for src, dst in self.settings.runtime_mounts or DEFAULT_RUNTIME_MOUNTS:
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
                stdin=asyncio.subprocess.DEVNULL,  # never inherit the user's tty
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
    # Changeset derivation (computed, never trusted from submission prose)
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_executable(manifest: repo.Manifest, rels: set[str]) -> list[str]:
        """Changed paths that carry any execute bit."""
        return sorted(
            rel
            for rel in rels
            if (entry := manifest.entry_for(rel)) is not None and entry.kind == "file" and (entry.mode or 0) & 0o111
        )

    @staticmethod
    def _collect_empty_dirs(manifest: repo.Manifest) -> list[str]:
        """Directories with no manifest entries beneath them (empty, as seen
        by the agent; the ``.cairn`` scaffolding is excluded by the filter)."""
        dirs = set(manifest.dirs())
        prefix_of_dir: set[str] = set()
        for rel in manifest.entries:
            parts = rel.split("/")
            for i in range(1, len(parts)):
                prefix_of_dir.add("/".join(parts[:i]))
        return sorted(dirs - prefix_of_dir)

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
