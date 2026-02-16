"""
Grail - Transparent Python for Monty.

A minimalist library for writing Monty code with full IDE support.
"""

__version__ = "2.0.0"

# Core functions
from grail.script import load, run

# Declarations (for .pym files)
from grail._external import external
from grail._input import Input

# Snapshot
from grail.snapshot import Snapshot

# Limits presets
from grail.limits import STRICT, DEFAULT, PERMISSIVE

# Errors
from grail.errors import (
    GrailError,
    ParseError,
    CheckError,
    InputError,
    ExternalError,
    ExecutionError,
    LimitError,
    OutputError,
)

# Check result types
from grail._types import CheckResult, CheckMessage

# Define public API
__all__ = [
    # Core
    "load",
    "run",
    # Declarations
    "external",
    "Input",
    # Snapshot
    "Snapshot",
    # Limits
    "STRICT",
    "DEFAULT",
    "PERMISSIVE",
    # Errors
    "GrailError",
    "ParseError",
    "CheckError",
    "InputError",
    "ExternalError",
    "ExecutionError",
    "LimitError",
    "OutputError",
    # Check results
    "CheckResult",
    "CheckMessage",
]
