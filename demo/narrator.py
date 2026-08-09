"""The Narrator: chapters own the prose, this renders it with *captured* output.

The whole point of the demo (guide §2.1, §2.2) is that the transcript cannot
drift: what is in ``WALKTHROUGH.md`` is what actually ran.  The Narrator has
two kinds of writers — prose (``say``/``code``) that the chapter author owns,
and capture (``shell``/``capture``) that records real output — plus
``prove``, which asserts the chapter's claim and *raises* on false.  A demo
that prints ✘ and carries on is the failure mode this design exists to
prevent.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO


class DemoAssertionError(AssertionError):
    """A chapter's claim was false; the run must stop and exit non-zero."""


class Narrator:
    """Accumulate the walkthrough markdown and run the chapter's commands.

    All output is written to the transcript as it happens (and echoed to the
    console), so a crash mid-run still leaves a readable partial file.
    """

    def __init__(self, transcript: Path, *, tee: TextIO | None = None) -> None:
        self.transcript = transcript
        self._handle = transcript.open("w", encoding="utf-8")
        self._tee = tee or sys.stdout
        self._assertions = 0

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._handle.close()

    # -- prose -------------------------------------------------------------

    def act(self, numeral: str, title: str) -> None:
        self._write(f"\n## Act {numeral} — {title}\n")

    def chapter(self, cid: str, title: str) -> None:
        self._write(f"\n### Chapter {cid}: {title}\n")

    def say(self, markdown: str) -> None:
        """Dedented prose paragraph(s); blank lines become paragraph breaks."""
        body = textwrap.dedent(markdown).strip()
        self._write(body + "\n")

    def code(self, source: str, lang: str = "python") -> None:
        """Show source the reader will type (code fences, not captures)."""
        self._write(f"\n```{lang}\n{textwrap.dedent(source).strip()}\n```\n")

    # -- capture -----------------------------------------------------------

    def capture(self, label: str, text: str) -> None:
        """A labelled block of *actually captured* output — the part of the
        transcript that cannot drift, because it was produced by the run."""
        self._write(f"\n```text\n{label}\n{text.rstrip()}\n```\n")

    def shell(self, argv: Sequence[str], **kw: object) -> str:
        """Echo ``$ <argv>``, run it as a subprocess, capture stdout+stderr.

        Returns the captured combined output (also recorded in the
        transcript).  Raises ``subprocess.CalledProcessError`` on a non-zero
        exit unless ``check=False`` is passed, in which case the output is
        still returned and the exit code is available to the caller.
        """
        cmd = [str(arg) for arg in argv]
        echo = "$ " + shlex.join(cmd)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            **kw,  # type: ignore[arg-type]
        )
        output = proc.stdout
        if proc.stderr:
            output += proc.stderr
        combined = output.rstrip()
        block = echo if not combined else f"{echo}\n{combined}"
        self._write(f"\n```text\n{block}\n```\n")
        if proc.returncode != 0 and kw.get("check", True):
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)
        return combined

    # -- assertion ---------------------------------------------------------

    def prove(self, claim: str, ok: bool) -> None:
        """Assert the chapter's claim, record it, and raise on false.

        The claim line lands in the transcript (``[ok]`` / ``[FAIL]``) and
        the count is tracked so the runner can report how many claims were
        verified.
        """
        self._assertions += 1
        if not ok:
            self._write(f"\n[FAIL] {claim}\n")
            raise DemoAssertionError(f"claim failed: {claim}")
        self._write(f"\n[ok] {claim}\n")

    @property
    def assertion_count(self) -> int:
        return self._assertions

    # -- internals ---------------------------------------------------------

    def _write(self, text: str) -> None:
        self._handle.write(text)
        self._handle.flush()
        if self._tee is not None:
            print(text.rstrip(), file=self._tee)
