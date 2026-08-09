"""Chapter registry and selection (guide §4.4).

Acts own the prose; every chapter calls ``narrator.prove()`` so a claim that
no longer holds fails the run loudly instead of printing a stale ✘.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from demo.chapters import act0, act1, act2, act3, act4

if TYPE_CHECKING:
    from demo.narrator import Narrator
    from demo.runner_ctx import ChapterContext

#: Act numeral -> (act module, title).  Imported eagerly so the module list is
#: stable regardless of which entry point is used.
ACT_MODULES = {
    "0": act0,
    "I": act1,
    "II": act2,
    "III": act3,
    "IV": act4,
}


def _chapter_lookup() -> dict[str, str]:
    """chapter id -> act numeral."""
    lookup: dict[str, str] = {}
    for numeral, module in ACT_MODULES.items():
        for cid, _title, _fn in module.CHAPTERS:
            lookup[cid] = numeral
    return lookup


def run_selected(narrator: "Narrator", options: object, fixture_root: object) -> None:
    """Sync entry point: dispatch into the (async) act runners."""
    from demo.runner_ctx import ChapterContext

    ctx = ChapterContext(
        options=options,
        project_root=fixture_root,
        home=fixture_root.parent / "home",
        act4_home=fixture_root.parent / "act4-home",
    )

    only = getattr(options, "only", None)
    act = getattr(options, "act", None)
    no_daemon = getattr(options, "no_daemon", False)

    if only:
        cid = only.zfill(2)
        lookup = _chapter_lookup()
        if cid not in lookup:
            raise SystemExit(f"unknown chapter id: {only} (expected 00-20)")
        module = ACT_MODULES[lookup[cid]]
        asyncio.run(module.run(narrator, ctx, only=cid))
        return

    if act:
        if act not in ACT_MODULES:
            raise SystemExit(f"unknown act: {act} (expected 0-4)")
        asyncio.run(ACT_MODULES[act].run(narrator, ctx, only=None))
        return

    numerals = ("0", "I", "II", "III")
    if not no_daemon:
        numerals += ("IV",)

    async def _run_all() -> None:
        for numeral in numerals:
            await ACT_MODULES[numeral].run(narrator, ctx, only=None)

    asyncio.run(_run_all())
