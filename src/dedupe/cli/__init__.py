"""CLI entrypoint and subcommand dispatch.

This package replaces the single-file `dedupe/cli.py` (912 lines) with
one module per subcommand. Each subcommand module exposes a
`register(sub)` function (registers its subparser onto the main
subparsers action) and an internal `_cmd_*` handler. `__init__.py`
holds only the `main()` entrypoint and the small list of subcommand
modules.

Adding a new subcommand becomes "create one new module + add one
import line below" — Open/Closed Principle in practice.

The package replaces but doesn't change the import path: `from
dedupe.cli import main` continues to work because Python imports
`__init__.py` when you `import dedupe.cli`.
"""

from __future__ import annotations

import sys

from dedupe.cli import (
    convert,
    find_similar,
    info,
    restore,
    scan,
    sweep,
)
from dedupe.cli.parser import EXIT_ERROR, EXIT_USAGE, build_parser, make_ui, setup_logging

# Order is significant: this controls the order subcommands appear in
# `dedupe --help`. Adding a new subcommand: append its module here.
SUBCOMMANDS = (
    scan,
    find_similar,
    restore,
    convert,
    info,
    sweep,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(SUBCOMMANDS)
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_USAGE

    setup_logging(getattr(args, "verbose", False), getattr(args, "quiet", False))
    ui = make_ui(args)

    try:
        return args.func(args, ui)
    except KeyboardInterrupt:
        ui.error("interrupted")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
