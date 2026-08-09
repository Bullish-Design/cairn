"""Act 0 — trust the runtime.

ch00: ``cairn doctor`` — verify the sandbox runtime by *doing the thing*
(launching a real sandbox), not by inspecting configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from demo.narrator import Narrator
    from demo.runner_ctx import ChapterContext

ACT_NUMERAL = "0"
ACT_TITLE = "Trust the runtime"


async def run(narrator: "Narrator", ctx: "ChapterContext", only: str | None = None) -> None:
    narrator.act(ACT_NUMERAL, ACT_TITLE)
    for cid, title, fn in CHAPTERS:
        if only and cid != only:
            continue
        narrator.chapter(cid, title)
        await fn(narrator, ctx)


async def _ch00_doctor(narrator: "Narrator", ctx: "ChapterContext") -> None:
    narrator.say(
        """
        Before any agent runs, the walkthrough verifies the sandbox runtime.
        ``cairn doctor`` answers the question that a green test suite cannot:
        *do the parts that must agree actually agree?*  It greps the argv the
        executor builds for the isolation flags, and then — the part that
        matters — launches a real sandbox and asserts from inside it that the
        host home is unreachable and the network is unreachable.
        """
    )
    output = narrator.shell([*ctx.cli_cmd("doctor", "--cairn-home", str(ctx.home), "--project-root", str(ctx.project_root))])
    narrator.say(
        """
        Two checks launch a real sandbox rather than reading config:
        ``sandbox launch`` (a probe process runs and prints) and
        ``isolation effective`` (a probe *inside* the sandbox reports whether
        the host home or the network leaked through).  Everything else here
        is configuration inspection — these two are the evidence.
        """
    )
    narrator.prove("doctor exits 0 on a healthy runtime", "sandbox starts and executes code" in output)
    narrator.prove("isolation verified from inside a real sandbox", "verified from inside the sandbox" in output)


CHAPTERS = [
    ("00", "cairn doctor", _ch00_doctor),
]
