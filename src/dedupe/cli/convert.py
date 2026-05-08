"""`dedupe convert` subcommand: parser config + handler."""

from __future__ import annotations

import argparse
from pathlib import Path

from dedupe.cli.output import format_bytes
from dedupe.cli.parser import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    add_global_flags,
    default_threads,
    flatten_list_arg,
)
from dedupe.ui import UI


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "convert",
        help="Convert images to a target format (originals untouched)",
        description=(
            "Walk a folder for HEIC/HEIF images (by default) and write a "
            "converted copy of each into a sibling <folder>-converted folder, "
            "mirroring the original layout. Originals are never modified."
        ),
    )
    add_global_flags(p)
    p.add_argument("folder", type=Path, help="Folder to scan for convertible images")
    p.add_argument(
        "--to",
        dest="target_format",
        default="jpeg",
        choices=["jpeg", "jpg", "png", "webp"],
        help="Target format (default: jpeg)",
    )
    p.add_argument(
        "--quality",
        type=int,
        default=92,
        metavar="N",
        help="Encoder quality 1-100 (JPEG/WebP only; default: 92)",
    )
    p.add_argument(
        "--source-ext",
        action="append",
        metavar="EXT",
        help=(
            "Source extension to include. Repeatable AND comma-separated "
            "lists accepted (leading dot optional). Default: .heic and .heif. "
            "Examples: --source-ext png,bmp,gif  /  --source-ext png --source-ext bmp"
        ),
    )
    p.add_argument(
        "--from-any",
        action="store_true",
        help=(
            "Convert every readable image format EXCEPT files that already "
            "match the target format. Mutually exclusive with --source-ext."
        ),
    )
    p.add_argument(
        "--output-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help="Where to write converted files (default: <folder>-converted)",
    )
    p.add_argument(
        "--archive-originals",
        action="store_true",
        help=(
            "After each successful conversion, MOVE the original into a "
            "sibling <folder>-heic folder (mirrored layout). Writes "
            "archive-manifest.json. Off by default."
        ),
    )
    p.add_argument(
        "--archive-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Where to move originals when --archive-originals is set " "(default: <folder>-heic)"
        ),
    )
    p.add_argument(
        "--in-place",
        action="store_true",
        help=(
            "Slideshow-friendly shortcut: write converted files INTO the "
            "source folder (alongside originals) and move originals to "
            "<folder>-heic. Equivalent to "
            "--output-folder <folder> --archive-originals. "
            "Cannot be combined with --output-folder."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be converted without writing files",
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
        help=f"Worker threads for conversion (default: {default_threads()})",
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
            "AND the basename."
        ),
    )
    p.set_defaults(func=_cmd_convert)


def _resolve_source_exts(args: argparse.Namespace, ui: UI) -> frozenset[str] | int:
    """Compute the source-extension set, or return EXIT_USAGE on conflict.

    Splitting this out trims the cyclomatic complexity of the main handler
    and makes the conflict-detection rules easier to audit.
    """
    from dedupe.convert import DEFAULT_SOURCE_EXTS  # noqa: PLC0415

    if args.from_any and args.source_ext:
        ui.error("--from-any cannot be combined with --source-ext")
        return EXIT_USAGE
    if args.from_any:
        from dedupe.scan import IMAGE_EXTENSIONS  # noqa: PLC0415

        target_aliases = (
            {".jpg", ".jpeg"}
            if args.target_format in {"jpeg", "jpg"}
            else {f".{args.target_format}"}
        )
        return frozenset(IMAGE_EXTENSIONS - target_aliases)
    if args.source_ext:
        return frozenset(flatten_list_arg(args.source_ext, lowercase=True, ensure_dot=True))
    return DEFAULT_SOURCE_EXTS


def _cmd_convert(args: argparse.Namespace, ui: UI) -> int:
    # Lazy import: keeps Pillow out of the import path of the other commands.
    from dedupe.convert import ConvertOptions, run_convert  # noqa: PLC0415

    folder: Path = args.folder

    # --in-place is a one-flag shortcut that writes converted files into
    # the source folder and archives originals. It conflicts with an
    # explicit --output-folder.
    if args.in_place and args.output_folder is not None:
        ui.error("--in-place cannot be combined with --output-folder")
        return EXIT_USAGE

    output_folder = (
        folder
        if args.in_place
        else (args.output_folder or folder.parent / f"{folder.name}-converted")
    )
    archive_originals = args.archive_originals or args.in_place

    source_exts_or_exit = _resolve_source_exts(args, ui)
    if isinstance(source_exts_or_exit, int):  # EXIT_USAGE
        return source_exts_or_exit
    source_exts = source_exts_or_exit

    opts = ConvertOptions(
        source=folder,
        output_folder=output_folder,
        target_format=args.target_format,
        quality=args.quality,
        source_exts=source_exts,
        dry_run=args.dry_run,
        recursive=args.recursive,
        threads=args.threads,
        include_hidden=args.include_hidden,
        follow_symlinks=args.follow_symlinks,
        archive_originals=archive_originals,
        archive_folder=args.archive_folder,
        exclude_patterns=tuple(flatten_list_arg(args.exclude)),
    )

    try:
        result = run_convert(opts, ui)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    if ui.config.json_mode:
        ui.emit_json(
            {
                "command": "convert",
                "dry_run": opts.dry_run,
                "source": str(opts.source),
                "output_folder": str(opts.output_folder),
                "archive_originals": opts.archive_originals,
                "archive_folder": (str(opts.archive_folder) if opts.archive_folder else None),
                "target_format": opts.target_format,
                "quality": opts.quality,
                "source_exts": sorted(opts.source_exts),
                "files_scanned": result.files_scanned,
                "files_converted": result.files_converted,
                "files_skipped": result.files_skipped,
                "files_archived": result.files_archived,
                "bytes_written": result.bytes_written,
                "errors": result.errors,
                "conversions": [
                    {"source": str(s), "output": str(d)} for s, d in result.conversions
                ],
                "archive_entries": [e.__dict__ for e in result.archive_entries],
            }
        )
    else:
        verb = "would convert" if opts.dry_run else "converted"
        ui.info("")
        ui.info("[bold]Summary[/bold]")
        ui.info(f"  files scanned:    {result.files_scanned}")
        ui.info(f"  files {verb}: {result.files_converted}")
        ui.info(f"  files skipped:    {result.files_skipped}")
        ui.info(
            f"  bytes written:    {result.bytes_written} " f"({format_bytes(result.bytes_written)})"
        )
        if opts.archive_originals:
            archive_verb = "would archive" if opts.dry_run else "archived"
            ui.info(f"  files {archive_verb}: {result.files_archived}")
        if result.files_converted and not opts.dry_run:
            ui.success(f"  output: {opts.output_folder}")
        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors else EXIT_OK
