"""Shared output-formatting helpers.

Right now this is one function (`format_bytes`). The module exists as
a home for future shared formatting utilities — summary tables, error
tabulation, JSON helpers — so they don't get spread across handlers.
"""

from __future__ import annotations


def format_bytes(n: int) -> str:
    """Render a byte count as the largest human-friendly unit."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"
