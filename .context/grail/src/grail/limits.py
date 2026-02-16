"""Resource limits parsing and presets."""

from __future__ import annotations

from typing import Any
import re

# Named presets (plain dicts)
STRICT: dict[str, Any] = {
    "max_memory": "8mb",
    "max_duration": "500ms",
    "max_recursion": 120,
}

DEFAULT: dict[str, Any] = {
    "max_memory": "16mb",
    "max_duration": "2s",
    "max_recursion": 200,
}

PERMISSIVE: dict[str, Any] = {
    "max_memory": "64mb",
    "max_duration": "5s",
    "max_recursion": 400,
}


def parse_memory_string(value: str) -> int:
    """
    Parse memory string to bytes.

    Examples:
        "16mb" -> 16777216
        "1gb" -> 1073741824
        "512kb" -> 524288

    Args:
        value: Memory string (e.g., "16mb", "1GB")

    Returns:
        Number of bytes

    Raises:
        ValueError: If format is invalid
    """
    value = value.lower().strip()

    # Match number and unit
    match = re.match(r"^(\d+(?:\.\d+)?)(kb|mb|gb)$", value)
    if not match:
        raise ValueError(f"Invalid memory format: {value}. Use format like '16mb', '1gb'")

    number, unit = match.groups()
    number = float(number)

    multipliers = {
        "kb": 1024,
        "mb": 1024 * 1024,
        "gb": 1024 * 1024 * 1024,
    }

    return int(number * multipliers[unit])


def parse_duration_string(value: str) -> float:
    """
    Parse duration string to seconds.

    Examples:
        "500ms" -> 0.5
        "2s" -> 2.0
        "1.5s" -> 1.5

    Args:
        value: Duration string (e.g., "500ms", "2s")

    Returns:
        Number of seconds

    Raises:
        ValueError: If format is invalid
    """
    value = value.lower().strip()

    # Match number and unit
    match = re.match(r"^(\d+(?:\.\d+)?)(ms|s)$", value)
    if not match:
        raise ValueError(f"Invalid duration format: {value}. Use format like '500ms', '2s'")

    number, unit = match.groups()
    number = float(number)

    if unit == "ms":
        return number / 1000.0

    return number


def parse_limits(limits: dict[str, Any]) -> dict[str, Any]:
    """
    Parse limits dict, converting string formats to native types
    and translating key names to Monty format.
    """
    parsed: dict[str, Any] = {}

    for key, value in limits.items():
        if key == "max_memory" and isinstance(value, str):
            parsed["max_memory"] = parse_memory_string(value)
        elif key == "max_memory":
            parsed["max_memory"] = value
        elif key == "max_duration" and isinstance(value, str):
            parsed["max_duration_secs"] = parse_duration_string(value)  # Key renamed
        elif key == "max_duration":
            parsed["max_duration_secs"] = float(value)  # Key renamed
        elif key == "max_recursion":
            parsed["max_recursion_depth"] = value  # Key renamed
        else:
            parsed[key] = value

    return parsed


def merge_limits(
    base: dict[str, Any] | None,
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Merge two limits dicts, with override taking precedence.

    Args:
        base: Base limits (e.g., from load())
        override: Override limits (e.g., from run())

    Returns:
        Merged limits dict
    """
    if base is None and override is None:
        return parse_limits(DEFAULT.copy())

    if base is None:
        return parse_limits(override.copy())

    if override is None:
        return parse_limits(base.copy())

    merged = base.copy()
    merged.update(override)
    return parse_limits(merged)
