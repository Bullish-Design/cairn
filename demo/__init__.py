"""Cairn demo: a runnable walkthrough that drives real agents through the full
lifecycle against a throwaway fixture and emits demo/out/WALKTHROUGH.md.

Run from the repository root:

    python -m demo
"""

from __future__ import annotations

from demo.narrator import Narrator

__all__ = ["Narrator"]
