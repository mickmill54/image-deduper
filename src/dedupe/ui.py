"""Console output wrapper.

The only module in the package that talks to stdout/stderr. Every other
module routes user-facing output through a `UI` instance so the same code
works in rich, quiet, and json modes without branching.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)


@dataclass(frozen=True)
class UIConfig:
    verbose: bool = False
    quiet: bool = False
    no_color: bool = False
    json_mode: bool = False


class UI:
    """Console wrapper that respects --quiet, --json, --no-color, NO_COLOR."""

    def __init__(self, config: UIConfig | None = None) -> None:
        self.config = config or UIConfig()
        env_no_color = bool(os.environ.get("NO_COLOR"))
        force_no_color = self.config.no_color or env_no_color
        self._console = Console(
            stderr=False,
            no_color=force_no_color,
            highlight=False,
            quiet=self.config.json_mode,
        )
        self._err_console = Console(
            stderr=True,
            no_color=force_no_color,
            highlight=False,
        )

    # --- level helpers -----------------------------------------------------

    def info(self, message: str) -> None:
        if self.config.quiet or self.config.json_mode:
            return
        self._console.print(message)

    def detail(self, message: str) -> None:
        if not self.config.verbose or self.config.quiet or self.config.json_mode:
            return
        self._console.print(f"[dim]{message}[/dim]")

    def success(self, message: str) -> None:
        if self.config.quiet or self.config.json_mode:
            return
        self._console.print(f"[green]{message}[/green]")

    def warn(self, message: str) -> None:
        if self.config.json_mode:
            return
        self._err_console.print(f"[yellow]warning:[/yellow] {message}")

    def error(self, message: str) -> None:
        if self.config.json_mode:
            # Errors still go to stderr in json mode, but as plain text so they
            # don't corrupt the json on stdout.
            print(f"error: {message}", file=sys.stderr)
            return
        self._err_console.print(f"[red]error:[/red] {message}")

    # --- structured output -------------------------------------------------

    def emit_json(self, payload: dict[str, Any]) -> None:
        """Write a single JSON document to stdout. Used in --json mode only."""
        if not self.config.json_mode:
            return
        json.dump(payload, sys.stdout, indent=2, default=str, sort_keys=True)
        sys.stdout.write("\n")
        sys.stdout.flush()

    # --- progress ---------------------------------------------------------

    @contextmanager
    def progress(self, description: str, total: int) -> Iterator[_ProgressHandle]:
        """Show a progress bar unless quiet/json. Yields a handle with .advance()."""
        if self.config.quiet or self.config.json_mode or total == 0:
            yield _NullProgress()
            return

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("[dim]{task.fields[current]}[/dim]"),
            TimeRemainingColumn(),
            console=self._console,
            transient=False,
        )
        task_id = progress.add_task(description, total=total, current="")
        with progress:
            yield _RichProgress(progress, task_id)


class _ProgressHandle:
    def advance(self, current: str = "") -> None:  # pragma: no cover - protocol
        raise NotImplementedError


class _NullProgress(_ProgressHandle):
    def advance(self, current: str = "") -> None:
        return


class _RichProgress(_ProgressHandle):
    def __init__(self, progress: Progress, task_id: TaskID) -> None:
        self._progress = progress
        self._task_id = task_id

    def advance(self, current: str = "") -> None:
        self._progress.update(self._task_id, advance=1, current=current)
