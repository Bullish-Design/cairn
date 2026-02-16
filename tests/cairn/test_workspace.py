from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import Fsdantic, MergeStrategy


@pytest.mark.asyncio
async def test_materialize_and_overlay_merge_with_fsdantic(tmp_path: Path) -> None:
    base = await Fsdantic.open(path=str(tmp_path / "base.db"))
    overlay = await Fsdantic.open(path=str(tmp_path / "overlay.db"))

    try:
        await base.files.write("shared/base.txt", "base")
        await overlay.files.write("shared/base.txt", "overlay")
        await overlay.files.write("shared/new.txt", "new")

        preview = await overlay.materialize.preview(base)
        preview_paths = {change.path for change in preview}
        assert "/shared/base.txt" in preview_paths
        assert "/shared/new.txt" in preview_paths

        target = tmp_path / "materialized"
        result = await overlay.materialize.to_disk(target, base=base, clean=True)
        assert result.files_written >= 2
        assert (target / "shared/base.txt").read_text(encoding="utf-8") == "overlay"
        assert (target / "shared/new.txt").read_text(encoding="utf-8") == "new"

        merge_result = await base.overlay.merge(overlay, strategy=MergeStrategy.OVERWRITE)
        assert merge_result.files_merged >= 2
        assert await base.files.read("shared/base.txt") == "overlay"
        assert await base.files.read("shared/new.txt") == "new"
    finally:
        await overlay.close()
        await base.close()
