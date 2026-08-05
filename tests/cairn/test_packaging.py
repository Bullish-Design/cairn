"""Packaging regression tests (review §2.1).

The published sdist/wheel must contain the full ``cairn`` package, not just
``py.typed``.  The Hatch ``include`` overrides in pyproject.toml restricted
both artifacts to a single marker file, so ``import cairn`` resolved to an
empty namespace package and ``cairn --help`` died with
``ModuleNotFoundError: No module named 'cairn.cli'``.

These tests assert the build configuration does not restrict the artifacts;
the M1 CI job builds both artifacts, installs them into a clean venv, and
runs ``cairn --help`` against them.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"


def _hatch_targets() -> dict[str, dict[str, object]]:
    with PYPROJECT.open("rb") as handle:
        config = tomllib.load(handle)
    return config["tool"]["hatch"]["build"]["targets"]


def test_wheel_target_does_not_restrict_to_py_typed_only() -> None:
    """The wheel must include the package modules, not just py.typed."""
    wheel = _hatch_targets()["wheel"]
    include = list(wheel.get("include", []))
    assert "src/cairn/py.typed" not in include, (
        "wheel 'include' restricts the artifact to src/cairn/py.typed only; the wheel ships no Python modules"
    )
    # The package root must be present so cairn/__init__.py and submodules
    # are actually packaged.
    assert wheel.get("packages") == ["src/cairn"]


def test_sdist_target_does_not_restrict_to_py_typed_only() -> None:
    """The sdist must reproduce the wheel; a py.typed-only sdist cannot."""
    sdist = _hatch_targets()["sdist"]
    include = list(sdist.get("include", []))
    assert "src/cairn/py.typed" not in include, (
        "sdist 'include' restricts the archive to src/cairn/py.typed only; the sdist cannot reproduce a usable wheel"
    )
