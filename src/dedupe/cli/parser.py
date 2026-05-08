"""Top-level argparse builder + cross-subcommand utilities.

Holds:
- `build_parser(subcommand_modules)` — assembles the parser by calling
  each subcommand module's `register(sub)`.
- Small CLI-input helpers used across subcommands: `default_threads`,
  `flatten_list_arg`, `add_global_flags`, `make_ui`, `setup_logging`.
- The exit-code constants the handlers use.

Each subcommand's parser config + handler lives in its own module
under `cli/`; this module only knows how to build the shell.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Iterable
from typing import Any

from dedupe import __version__
from dedupe.ui import UI, UIConfig

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # argparse default
EXIT_PARTIAL = 3

# Type alias for "anything with a `register(sub_parsers_action)` callable."
# Each subcommand module under `cli/` exposes such a function. Pyright
# can't statically check duck-typed module attributes against a Protocol
# (modules are `ModuleType`), so we accept `Any` and the contract is
# enforced by convention + manual review.
_SubcommandModule = Any


def default_threads() -> int:
    """Default thread count for parallel-friendly subcommands."""
    return os.cpu_count() or 4


def flatten_list_arg(
    raw: list[str] | None, *, lowercase: bool = False, ensure_dot: bool = False
) -> list[str]:
    """Flatten a repeatable list flag that may also use comma-separated values.

    Supports all of these equivalently:
        --flag a --flag b
        --flag a,b
        --flag a,b --flag c

    Tokens are stripped; empty tokens are dropped. Returns [] if raw is None.
    `lowercase` and `ensure_dot` control extension-style normalization.
    """
    if not raw:
        return []
    out: list[str] = []
    for entry in raw:
        for token in entry.split(","):
            t = token.strip()
            if not t:
                continue
            if lowercase:
                t = t.lower()
            if ensure_dot and not t.startswith("."):
                t = f".{t}"
            out.append(t)
    return out


def add_global_flags(parser: argparse.ArgumentParser) -> None:
    """Verbosity / output-mode flags that every subcommand inherits."""
    g = parser.add_argument_group("output")
    g.add_argument("--verbose", "-v", action="store_true", help="More detail")
    g.add_argument("--quiet", "-q", action="store_true", help="Errors only")
    g.add_argument(
        "--no-color",
        action="store_true",
        help="Disable color output (also respects NO_COLOR env var)",
    )
    g.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Machine-readable JSON output",
    )


def make_ui(args: argparse.Namespace) -> UI:
    return UI(
        UIConfig(
            verbose=getattr(args, "verbose", False),
            quiet=getattr(args, "quiet", False),
            no_color=getattr(args, "no_color", False),
            json_mode=getattr(args, "json_mode", False),
        )
    )


def setup_logging(verbose: bool, quiet: bool) -> None:
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_parser(
    subcommand_modules: Iterable[_SubcommandModule],
) -> argparse.ArgumentParser:
    """Assemble the dedupe parser by registering each subcommand's subparser."""
    parser = argparse.ArgumentParser(
        prog="dedupe",
        description="Find and quarantine duplicate image files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  dedupe scan ~/Pictures/slideshow\n"
            "  dedupe scan ~/Pictures/slideshow --dry-run\n"
            "  dedupe find-similar ~/Pictures/slideshow --threshold 3\n"
            "  dedupe restore ~/Pictures/slideshow-dups\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"dedupe {__version__}")
    add_global_flags(parser)

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for mod in subcommand_modules:
        mod.register(sub)
    return parser
