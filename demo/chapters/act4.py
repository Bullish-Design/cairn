"""Placeholder act module — filled in during the build."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from demo.narrator import Narrator
    from demo.runner_ctx import ChapterContext

ACT_NUMERAL = "4"
ACT_TITLE = "placeholder"
CHAPTERS: list = []


async def run(narrator: "Narrator", ctx: "ChapterContext", only: str | None = None) -> None:
    narrator.act(ACT_NUMERAL, ACT_TITLE)
    for cid, title, fn in CHAPTERS:
        if only and cid != only:
            continue
        narrator.chapter(cid, title)
        await fn(narrator, ctx)
