"""`dedupe info` subcommand: parser config + handler."""

from __future__ import annotations

import argparse
from pathlib import Path

from dedupe.cli.output import format_bytes
from dedupe.cli.parser import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_PARTIAL,
    add_global_flags,
    flatten_list_arg,
)
from dedupe.ui import UI


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "info",
        help="Print folder stats: file count by extension, total size, etc.",
        description=(
            "Walk a folder (read-only) and print stats: total files, "
            "image vs non-image counts, total size, breakdown by "
            "extension, hidden files, broken symlinks. Use --json for "
            "machine output."
        ),
    )
    add_global_flags(p)
    p.add_argument("folder", type=Path, help="Folder to inspect")
    recurse = p.add_mutually_exclusive_group()
    recurse.add_argument(
        "--recursive",
        "-r",
        dest="recursive",
        action="store_true",
        default=True,
        help="Recurse into subdirectories (default)",
    )
    recurse.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="Do not recurse into subdirectories",
    )
    p.add_argument(
        "--exclude-hidden",
        dest="include_hidden",
        action="store_false",
        default=True,
        help="Exclude dotfiles from counts (default: included)",
    )
    p.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks (default: skip)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help=(
            "Glob pattern to exclude (repeatable; comma-separated lists "
            "also accepted). Matched against the path relative to <folder> "
            "AND the basename."
        ),
    )
    p.set_defaults(func=_cmd_info)


def _cmd_info(args: argparse.Namespace, ui: UI) -> int:
    from dedupe.info import InfoOptions, run_info  # noqa: PLC0415

    opts = InfoOptions(
        source=args.folder,
        recursive=args.recursive,
        include_hidden=args.include_hidden,
        follow_symlinks=args.follow_symlinks,
        exclude_patterns=tuple(flatten_list_arg(args.exclude)),
    )

    try:
        result = run_info(opts, ui)
    except (FileNotFoundError, NotADirectoryError) as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    if ui.config.json_mode:
        ui.emit_json(
            {
                "command": "info",
                "source": str(result.source),
                "total_files": result.total_files,
                "image_files": result.image_files,
                "non_image_files": result.non_image_files,
                "hidden_files": result.hidden_files,
                "broken_symlinks": result.broken_symlinks,
                "total_size_bytes": result.total_size_bytes,
                "image_size_bytes": result.image_size_bytes,
                "by_extension": result.by_extension,
                "size_by_extension": result.size_by_extension,
                "errors": result.errors,
            }
        )
    else:
        ui.info("")
        ui.info("[bold]Summary[/bold]")
        ui.info(f"  total files:     {result.total_files}")
        ui.info(f"  image files:     {result.image_files}")
        ui.info(f"  non-image files: {result.non_image_files}")
        ui.info(f"  hidden files:    {result.hidden_files}")
        if result.broken_symlinks:
            ui.warn(f"  broken symlinks: {result.broken_symlinks}")
        ui.info(
            f"  total size:      {result.total_size_bytes} "
            f"({format_bytes(result.total_size_bytes)})"
        )
        ui.info(
            f"  image size:      {result.image_size_bytes} "
            f"({format_bytes(result.image_size_bytes)})"
        )

        if result.by_extension:
            ui.info("")
            ui.info("[bold]By extension[/bold] (count · size)")
            # Sort by count desc, then ext asc.
            for ext, count in sorted(result.by_extension.items(), key=lambda kv: (-kv[1], kv[0])):
                size = result.size_by_extension.get(ext, 0)
                ui.info(f"  {ext:<10} {count:>6}   {format_bytes(size)}")

        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors else EXIT_OK
