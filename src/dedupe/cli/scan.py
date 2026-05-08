"""`dedupe scan` subcommand: parser config + handler."""

from __future__ import annotations

import argparse
from pathlib import Path

from dedupe.cli.output import format_bytes
from dedupe.cli.parser import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_PARTIAL,
    add_global_flags,
    default_threads,
    flatten_list_arg,
)
from dedupe.ui import UI


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "scan",
        help="Find exact duplicates and move them to quarantine",
        description=(
            "Scan a folder for byte-for-byte duplicate images (SHA-256). "
            "Keeps one copy of each group; moves the rest to a sibling dups "
            "folder. Never deletes."
        ),
    )
    add_global_flags(p)
    p.add_argument("folder", type=Path, help="Folder to scan")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be moved without moving anything",
    )
    p.add_argument(
        "--dups-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help="Where to move duplicates (default: <folder>-dups, sibling of folder)",
    )
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
        "--threads",
        type=int,
        default=default_threads(),
        metavar="N",
        help=f"Worker threads for hashing (default: {default_threads()})",
    )
    p.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include dotfiles (default: skip)",
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
            "AND the basename. e.g. --exclude 'exports/*' --exclude '*.tmp'"
        ),
    )
    p.set_defaults(func=_cmd_scan)


def _cmd_scan(args: argparse.Namespace, ui: UI) -> int:
    # Lazy import: keeps `dedupe --help` / `dedupe --version` startup snappy.
    from dedupe.scan import ScanOptions, run_scan  # noqa: PLC0415

    # Resolve to absolute so `dedupe scan .` lands the dups folder as a
    # sibling of the source rather than inside it (see #43). Path("."),
    # Path("./photos"), and similar collapse `.parent` to themselves
    # otherwise.
    folder: Path = args.folder.resolve()
    dups_folder: Path = args.dups_folder or folder.parent / f"{folder.name}-dups"

    opts = ScanOptions(
        source=folder,
        dups_folder=dups_folder,
        dry_run=args.dry_run,
        recursive=args.recursive,
        threads=args.threads,
        include_hidden=args.include_hidden,
        follow_symlinks=args.follow_symlinks,
        exclude_patterns=tuple(flatten_list_arg(args.exclude)),
    )

    try:
        result = run_scan(opts, ui)
    except (FileNotFoundError, NotADirectoryError) as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    if ui.config.json_mode:
        ui.emit_json(
            {
                "command": "scan",
                "dry_run": opts.dry_run,
                "source": str(opts.source),
                "dups_folder": str(opts.dups_folder),
                "files_scanned": result.files_scanned,
                "duplicate_groups": result.duplicate_groups,
                "files_moved": result.files_moved,
                "bytes_reclaimed": result.bytes_reclaimed,
                "errors": result.errors,
                "moves": [m.__dict__ for m in result.moves],
            }
        )
    else:
        verb = "would move" if opts.dry_run else "moved"
        ui.info("")
        ui.info("[bold]Summary[/bold]")
        ui.info(f"  files scanned:    {result.files_scanned}")
        ui.info(f"  duplicate groups: {result.duplicate_groups}")
        ui.info(f"  files {verb}:      {result.files_moved}")
        ui.info(
            f"  bytes reclaimed:  {result.bytes_reclaimed} "
            f"({format_bytes(result.bytes_reclaimed)})"
        )
        if result.files_moved and not opts.dry_run:
            ui.success(f"  manifest: {opts.dups_folder}/manifest.json")
        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors else EXIT_OK
