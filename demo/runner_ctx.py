"""Shared context for the demo chapters: paths, options, and helpers.

One ``ChapterContext`` per run; each act builds its own orchestrator over the
same fixture (guide §2.5: ``initialize()`` binds the control socket, so two
live orchestrators on one CAIRN_HOME conflict — Act IV uses its own home).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from cairn.core.exceptions import CairnError
from cairn.runtime.settings import ExecutorSettings, OrchestratorSettings


@dataclass(frozen=True)
class DemoOptions:
    out_dir: Path
    transcript_path: Path
    keep: bool = False
    no_daemon: bool = False
    include_recovery: bool = False
    only: str | None = None
    act: str | None = None

    @property
    def project_root(self) -> Path:
        return self.out_dir / "project"


@dataclass
class ChapterContext:
    """Everything a chapter needs: fixture paths, shared home, options, and
    the interpreter used to run the real CLI in subprocesses."""

    options: DemoOptions
    project_root: Path
    home: Path
    act4_home: Path

    #: Scratch dirs under demo/out/ that a non-``--keep`` run removes.
    SCRATCH_DIRS = (
        "project",
        "home",
        "act4-home",
        "doctor-project",
        "doctor-home",
        "act3-workspaces",
        "act4-daemon.log",
    )

    def __post_init__(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.act4_home.mkdir(parents=True, exist_ok=True)

    @property
    def out_dir(self) -> Path:
        return self.options.out_dir

    def cli_env(self, *, project_root: Path | None = None, home: Path | None = None) -> dict[str, str]:
        """Environment for a ``python -m cairn.cli.cli`` subprocess.

        Flags are passed *after* the subcommand (guide §4.3: the position that
        works in both the fixed and unfixed CLI, so the transcript stays valid
        for readers on an older checkout).
        """
        env = dict(os.environ)
        for name in list(env):
            if name.startswith(("CAIRN_PATHS_", "CAIRN_ORCHESTRATOR_")):
                env.pop(name)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def cli_cmd(self, *argv: str) -> list[str]:
        return [sys.executable, "-m", "cairn.cli.cli", *argv]

    def make_orchestrator(
        self,
        *,
        project_root: Path | None = None,
        home: Path | None = None,
        config: OrchestratorSettings | None = None,
        executor: ExecutorSettings | None = None,
        provider: object = None,
    ):
        """Build a CairnOrchestrator and assert the isolation post-condition:
        the orchestrator must be bound to the fixture, never the checkout."""
        from cairn.orchestrator.orchestrator import CairnOrchestrator

        root = project_root or self.project_root
        orch = CairnOrchestrator(
            project_root=root,
            cairn_home=home or self.home,
            config=config or OrchestratorSettings(),
            executor_settings=executor or ExecutorSettings(),
            code_provider=provider,
        )
        # Post-condition guard (guide §2.3): this is what would have caught
        # the pre-subcommand flag bug immediately.
        if orch.project_root != Path(root).resolve():
            raise CairnError(
                f"orchestrator bound to {orch.project_root}, expected fixture {root}",
                error_code="DEMO_ISOLATION",
            )
        return orch
