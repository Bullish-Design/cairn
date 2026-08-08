"""Shared resolution of the real-sandbox test runtime.

The real-sandbox tests need bubblewrap plus an interpreter that can be
bind-mounted into the sandbox.  Both are resolved here, once, so the rule
cannot drift between test modules — it previously lived in two copies and both
carried the same bug (``"/nix/store" in Path.parts`` is never true, because
``parts`` splits into ``('/', 'nix', 'store', ...)``).

Resolution order:

1. ``CAIRN_TEST_BWRAP`` / ``CAIRN_TEST_PYTHON`` — explicit overrides for
   ad-hoc runs.
2. ``CAIRN_EXECUTOR_BWRAP_PATH`` / ``CAIRN_EXECUTOR_PYTHON_PATH`` — set by
   ``devenv.nix``, so ``devenv test`` and CI always resolve.
3. Discovery: ``bwrap`` on ``PATH``, and the running interpreter when it lives
   in the Nix store.

**Why this is loud.**  These tests cover the sandbox isolation boundary.  A
green suite with them silently skipped is indistinguishable from a green suite
with them run, which is exactly how the resolution bug survived.  When the
runtime cannot be resolved a warning is emitted; set
``CAIRN_REQUIRE_SANDBOX_TESTS=1`` to turn the skip into a hard collection
error instead (recommended wherever the sandbox is expected to exist).
"""

from __future__ import annotations

import os
import shutil
import sys
import warnings
from pathlib import Path

import pytest

NIX_STORE = Path("/nix/store")


def _env_or(*names: str) -> str | None:
    """First non-empty value among ``names``."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _resolve_bwrap() -> str | None:
    return _env_or("CAIRN_TEST_BWRAP", "CAIRN_EXECUTOR_BWRAP_PATH") or shutil.which("bwrap")


def _resolve_sandbox_python() -> str | None:
    """Resolve an interpreter that can be bind-mounted into the sandbox.

    Falls back to the running interpreter when it resolves into the Nix store
    (the devenv venv's ``python`` is a symlink into it).
    """
    configured = _env_or("CAIRN_TEST_PYTHON", "CAIRN_EXECUTOR_PYTHON_PATH")
    if configured:
        return str(Path(configured).resolve())
    resolved = Path(sys.executable).resolve()
    if resolved.is_relative_to(NIX_STORE):
        return str(resolved)
    return None


BWRAP = _resolve_bwrap()
SANDBOX_PYTHON = _resolve_sandbox_python()
SANDBOX_AVAILABLE = bool(BWRAP and SANDBOX_PYTHON)


def _missing_reason() -> str:
    missing = []
    if not BWRAP:
        missing.append("bubblewrap (set CAIRN_TEST_BWRAP)")
    if not SANDBOX_PYTHON:
        missing.append("a bind-mountable interpreter (set CAIRN_TEST_PYTHON)")
    return " and ".join(missing)


SKIP_REASON = f"real-sandbox runtime unavailable: missing {_missing_reason()}" if not SANDBOX_AVAILABLE else ""

_REQUIRED = os.environ.get("CAIRN_REQUIRE_SANDBOX_TESTS", "").lower() in {"1", "true", "yes"}

if not SANDBOX_AVAILABLE:
    if _REQUIRED:
        raise RuntimeError(
            f"CAIRN_REQUIRE_SANDBOX_TESTS is set, but {SKIP_REASON}. "
            "These tests cover the sandbox isolation boundary and must not be skipped here."
        )
    warnings.warn(
        f"Skipping real-sandbox tests — {SKIP_REASON}. "
        "Sandbox isolation coverage is NOT being exercised in this run. "
        "Run inside `devenv shell` (which sets CAIRN_EXECUTOR_*), or set "
        "CAIRN_REQUIRE_SANDBOX_TESTS=1 to make this an error.",
        stacklevel=2,
    )

#: Apply to any test that launches a real bwrap sandbox.
requires_sandbox = pytest.mark.skipif(not SANDBOX_AVAILABLE, reason=SKIP_REASON or "sandbox available")
