"""Sample entrypoint seeded from the live fixture tree."""

from .utils import greet


def main() -> str:
    return greet("cairn")
