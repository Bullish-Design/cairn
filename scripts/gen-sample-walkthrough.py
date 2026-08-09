#!/usr/bin/env python3
"""Generate docs/sample-walkthrough.md from a full demo run.

Runs ``python -m demo --keep`` (full: all five acts) and relativizes the
repo-root absolute paths so the committed sample reads cleanly on GitHub.

Run inside the devenv shell (needs bwrap + CAIRN_EXECUTOR_*):

    python scripts/gen-sample-walkthrough.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = REPO_ROOT / "demo" / "out" / "WALKTHROUGH.md"
TARGET = REPO_ROOT / "docs" / "sample-walkthrough.md"


def main() -> int:
    subprocess.run(
        [sys.executable, "-m", "demo", "--keep"],
        cwd=REPO_ROOT,
        check=True,
    )
    text = TRANSCRIPT.read_text(encoding="utf-8")
    text = text.replace(str(REPO_ROOT), "$REPO")
    TARGET.write_text(text, encoding="utf-8")
    print(f"sample written to {TARGET} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
