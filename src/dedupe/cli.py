"""Argparse-based CLI entry point.

Subcommands wire user input to the scan/similar/restore modules. This
module never touches the filesystem itself; it parses, validates, and
dispatches.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dedupe import __version__
from dedupe.ui import UI, UIConfig

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # argparse default
EXIT_PARTIAL = 3


def _default_threads() -> int:
    return os.cpu_count() or 4


def _flatten_list_arg(
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


def _build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 — flat subparser config is the readable shape
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

    # Global flags (also added per-subparser so they work in either position).
    _add_global_flags(parser)

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # scan
    scan_p = sub.add_parser(
        "scan",
        help="Find exact duplicates and move them to quarantine",
        description=(
            "Scan a folder for byte-for-byte duplicate images (SHA-256). "
            "Keeps one copy of each group; moves the rest to a sibling dups "
            "folder. Never deletes."
        ),
    )
    _add_global_flags(scan_p)
    scan_p.add_argument("folder", type=Path, help="Folder to scan")
    scan_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be moved without moving anything",
    )
    scan_p.add_argument(
        "--dups-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help="Where to move duplicates (default: <folder>-dups, sibling of folder)",
    )
    recurse = scan_p.add_mutually_exclusive_group()
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
    scan_p.add_argument(
        "--threads",
        type=int,
        default=_default_threads(),
        metavar="N",
        help=f"Worker threads for hashing (default: {_default_threads()})",
    )
    scan_p.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include dotfiles (default: skip)",
    )
    scan_p.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks (default: skip)",
    )
    scan_p.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help=(
            "Glob pattern to exclude (repeatable; comma-separated lists "
            "also accepted). Matched against the path relative to <folder> "
            "AND the basename. e.g. --exclude 'exports/*' --exclude '*.tmp'"
        ),
    )
    scan_p.set_defaults(func=_cmd_scan)

    # find-similar
    sim_p = sub.add_parser(
        "find-similar",
        help="Report visually-similar photos (read-only, no moves)",
        description=(
            "Find visually-similar (but not byte-identical) photos using "
            "perceptual hashing. Report only — never moves files. Outputs "
            "a self-contained HTML report with side-by-side thumbnails."
        ),
    )
    _add_global_flags(sim_p)
    sim_p.add_argument("folder", type=Path, help="Folder to scan")
    sim_p.add_argument(
        "--threshold",
        type=int,
        default=5,
        metavar="N",
        help="Hamming distance threshold (default: 5; lower = stricter)",
    )
    sim_p.add_argument(
        "--report",
        type=Path,
        default=Path("similar-report.html"),
        metavar="PATH",
        help="HTML report output path (default: similar-report.html in cwd)",
    )
    sim_p.set_defaults(func=_cmd_find_similar)

    # restore
    rst_p = sub.add_parser(
        "restore",
        help="Move quarantined files back to their original locations",
        description=(
            "Read the manifest in <dups-folder> and move every quarantined "
            "file back to its original location. Refuses to overwrite if a "
            "file already exists at the original path."
        ),
    )
    _add_global_flags(rst_p)
    rst_p.add_argument("dups_folder", type=Path, help="Quarantine folder containing manifest.json")
    rst_p.set_defaults(func=_cmd_restore)

    # convert
    conv_p = sub.add_parser(
        "convert",
        help="Convert images to a target format (originals untouched)",
        description=(
            "Walk a folder for HEIC/HEIF images (by default) and write a "
            "converted copy of each into a sibling <folder>-converted folder, "
            "mirroring the original layout. Originals are never modified."
        ),
    )
    _add_global_flags(conv_p)
    conv_p.add_argument("folder", type=Path, help="Folder to scan for convertible images")
    conv_p.add_argument(
        "--to",
        dest="target_format",
        default="jpeg",
        choices=["jpeg", "jpg", "png", "webp"],
        help="Target format (default: jpeg)",
    )
    conv_p.add_argument(
        "--quality",
        type=int,
        default=92,
        metavar="N",
        help="Encoder quality 1-100 (JPEG/WebP only; default: 92)",
    )
    conv_p.add_argument(
        "--source-ext",
        action="append",
        metavar="EXT",
        help=(
            "Source extension to include. Repeatable AND comma-separated "
            "lists accepted (leading dot optional). Default: .heic and .heif. "
            "Examples: --source-ext png,bmp,gif  /  --source-ext png --source-ext bmp"
        ),
    )
    conv_p.add_argument(
        "--from-any",
        action="store_true",
        help=(
            "Convert every readable image format EXCEPT files that already "
            "match the target format. Mutually exclusive with --source-ext."
        ),
    )
    conv_p.add_argument(
        "--output-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help="Where to write converted files (default: <folder>-converted)",
    )
    conv_p.add_argument(
        "--archive-originals",
        action="store_true",
        help=(
            "After each successful conversion, MOVE the original into a "
            "sibling <folder>-heic folder (mirrored layout). Writes "
            "archive-manifest.json. Off by default."
        ),
    )
    conv_p.add_argument(
        "--archive-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Where to move originals when --archive-originals is set " "(default: <folder>-heic)"
        ),
    )
    conv_p.add_argument(
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
    conv_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be converted without writing files",
    )
    conv_recurse = conv_p.add_mutually_exclusive_group()
    conv_recurse.add_argument(
        "--recursive",
        "-r",
        dest="recursive",
        action="store_true",
        default=True,
        help="Recurse into subdirectories (default)",
    )
    conv_recurse.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="Do not recurse into subdirectories",
    )
    conv_p.add_argument(
        "--threads",
        type=int,
        default=_default_threads(),
        metavar="N",
        help=f"Worker threads for conversion (default: {_default_threads()})",
    )
    conv_p.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include dotfiles (default: skip)",
    )
    conv_p.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks (default: skip)",
    )
    conv_p.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help=(
            "Glob pattern to exclude (repeatable; comma-separated lists "
            "also accepted). Matched against the path relative to <folder> "
            "AND the basename."
        ),
    )
    conv_p.set_defaults(func=_cmd_convert)

    # info
    info_p = sub.add_parser(
        "info",
        help="Print folder stats: file count by extension, total size, etc.",
        description=(
            "Walk a folder (read-only) and print stats: total files, "
            "image vs non-image counts, total size, breakdown by "
            "extension, hidden files, broken symlinks. Use --json for "
            "machine output."
        ),
    )
    _add_global_flags(info_p)
    info_p.add_argument("folder", type=Path, help="Folder to inspect")
    info_recurse = info_p.add_mutually_exclusive_group()
    info_recurse.add_argument(
        "--recursive",
        "-r",
        dest="recursive",
        action="store_true",
        default=True,
        help="Recurse into subdirectories (default)",
    )
    info_recurse.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="Do not recurse into subdirectories",
    )
    info_p.add_argument(
        "--exclude-hidden",
        dest="include_hidden",
        action="store_false",
        default=True,
        help="Exclude dotfiles from counts (default: included)",
    )
    info_p.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks (default: skip)",
    )
    info_p.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help=(
            "Glob pattern to exclude (repeatable; comma-separated lists "
            "also accepted). Matched against the path relative to <folder> "
            "AND the basename."
        ),
    )
    info_p.set_defaults(func=_cmd_info)

    # sweep
    sweep_p = sub.add_parser(
        "sweep",
        help="Move/delete non-photo content (junk files, etc.) out of source folders",
        description=(
            "Walk a folder for non-photo content and clean it out. "
            "Today: --junk mode for auto-generated OS metadata files "
            "(Thumbs.db, .DS_Store, desktop.ini, .AppleDouble). Default "
            "action for junk files is DELETE (with manifest log) since "
            "they're auto-regenerated by their OS. Pass "
            "--quarantine-junk to move them to a sibling folder instead."
        ),
    )
    _add_global_flags(sweep_p)
    sweep_p.add_argument("folder", type=Path, help="Folder to sweep")
    sweep_p.add_argument(
        "--junk",
        dest="sweep_junk",
        action="store_true",
        help=(
            "Sweep auto-generated OS metadata files (Thumbs.db, .DS_Store, "
            "desktop.ini, .AppleDouble). Default action: delete + log."
        ),
    )
    sweep_p.add_argument(
        "--quarantine-junk",
        action="store_true",
        help=(
            "Instead of deleting, MOVE junk files to a sibling folder "
            "(default: <folder>-junk) mirroring layout. Safer but creates a "
            "tree of nested empty-ish dirs."
        ),
    )
    sweep_p.add_argument(
        "--junk-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help=("Where to move junk files when --quarantine-junk is set " "(default: <folder>-junk)"),
    )
    sweep_p.add_argument(
        "--log-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Where to write the sweep manifest in delete mode "
            "(default: <folder>-sweep-log). Ignored with --quarantine-junk "
            "(manifest goes alongside the quarantined files)."
        ),
    )
    sweep_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted/moved without changing anything",
    )
    sweep_recurse = sweep_p.add_mutually_exclusive_group()
    sweep_recurse.add_argument(
        "--recursive",
        "-r",
        dest="recursive",
        action="store_true",
        default=True,
        help="Recurse into subdirectories (default)",
    )
    sweep_recurse.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="Do not recurse into subdirectories",
    )
    sweep_p.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks (default: skip)",
    )
    sweep_p.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help=(
            "Glob pattern to exclude (repeatable; comma-separated lists "
            "also accepted). Matched against the path relative to <folder> "
            "AND the basename."
        ),
    )
    sweep_p.set_defaults(func=_cmd_sweep)

    return parser


def _add_global_flags(parser: argparse.ArgumentParser) -> None:
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


def _make_ui(args: argparse.Namespace) -> UI:
    return UI(
        UIConfig(
            verbose=getattr(args, "verbose", False),
            quiet=getattr(args, "quiet", False),
            no_color=getattr(args, "no_color", False),
            json_mode=getattr(args, "json_mode", False),
        )
    )


def _setup_logging(verbose: bool, quiet: bool) -> None:
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


def _format_bytes(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"


# --- command handlers --------------------------------------------------


def _cmd_scan(args: argparse.Namespace, ui: UI) -> int:
    # Lazy import: keeps `dedupe --help` / `dedupe --version` startup snappy.
    from dedupe.scan import ScanOptions, run_scan  # noqa: PLC0415

    folder: Path = args.folder
    dups_folder: Path = args.dups_folder or folder.parent / f"{folder.name}-dups"

    opts = ScanOptions(
        source=folder,
        dups_folder=dups_folder,
        dry_run=args.dry_run,
        recursive=args.recursive,
        threads=args.threads,
        include_hidden=args.include_hidden,
        follow_symlinks=args.follow_symlinks,
        exclude_patterns=tuple(_flatten_list_arg(args.exclude)),
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
            f"({_format_bytes(result.bytes_reclaimed)})"
        )
        if result.files_moved and not opts.dry_run:
            ui.success(f"  manifest: {opts.dups_folder}/manifest.json")
        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors else EXIT_OK


def _cmd_find_similar(args: argparse.Namespace, ui: UI) -> int:
    # Lazy import: avoids loading Pillow/imagehash unless this command is used.
    from dedupe.similar import SimilarOptions, run_find_similar  # noqa: PLC0415

    opts = SimilarOptions(
        source=args.folder,
        threshold=args.threshold,
        report_path=args.report,
    )
    try:
        result = run_find_similar(opts, ui)
    except (FileNotFoundError, NotADirectoryError) as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    if ui.config.json_mode:
        ui.emit_json(
            {
                "command": "find-similar",
                "source": str(opts.source),
                "threshold": opts.threshold,
                "report_path": str(opts.report_path),
                "files_scanned": result.files_scanned,
                "groups": [
                    {"phash_anchor": g.phash_anchor, "members": [str(p) for p in g.members]}
                    for g in result.groups
                ],
                "errors": result.errors,
            }
        )
    else:
        ui.info("")
        ui.info("[bold]Summary[/bold]")
        ui.info(f"  files scanned:  {result.files_scanned}")
        ui.info(f"  similar groups: {len(result.groups)}")
        if result.groups:
            ui.success(f"  report: {opts.report_path}")
        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors else EXIT_OK


def _cmd_restore(args: argparse.Namespace, ui: UI) -> int:
    # Lazy import: keeps non-restore paths from pulling restore module symbols.
    from dedupe.restore import RestoreOptions, run_restore  # noqa: PLC0415

    opts = RestoreOptions(dups_folder=args.dups_folder)
    try:
        result = run_restore(opts, ui)
    except (FileNotFoundError, ValueError) as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    if ui.config.json_mode:
        ui.emit_json(
            {
                "command": "restore",
                "dups_folder": str(opts.dups_folder),
                "files_restored": result.files_restored,
                "files_skipped": result.files_skipped,
                "conflicts": result.conflicts,
                "errors": result.errors,
            }
        )
    else:
        ui.info("")
        ui.info("[bold]Summary[/bold]")
        ui.info(f"  files restored: {result.files_restored}")
        ui.info(f"  files skipped:  {result.files_skipped}")
        if result.conflicts:
            ui.warn(f"  {len(result.conflicts)} conflict(s) — see above")
        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors or result.conflicts else EXIT_OK


def _cmd_convert(args: argparse.Namespace, ui: UI) -> int:  # noqa: PLR0912 — option-build branches are intentionally linear
    # Lazy import: keeps Pillow out of the import path of the other commands.
    from dedupe.convert import (  # noqa: PLC0415
        DEFAULT_SOURCE_EXTS,
        ConvertOptions,
        run_convert,
    )

    folder: Path = args.folder

    # --in-place is a one-flag shortcut that writes converted files into
    # the source folder and archives originals. It conflicts with an
    # explicit --output-folder.
    if args.in_place and args.output_folder is not None:
        ui.error("--in-place cannot be combined with --output-folder")
        return EXIT_USAGE

    output_folder: Path
    if args.in_place:
        output_folder = folder
    else:
        output_folder = args.output_folder or folder.parent / f"{folder.name}-converted"

    archive_originals = args.archive_originals or args.in_place

    # --from-any conflicts with --source-ext (ambiguous intent).
    if args.from_any and args.source_ext:
        ui.error("--from-any cannot be combined with --source-ext")
        return EXIT_USAGE

    if args.from_any:
        # Lazy import: Pillow is loaded only when convert actually runs.
        from dedupe.scan import IMAGE_EXTENSIONS  # noqa: PLC0415

        target_aliases = (
            {".jpg", ".jpeg"}
            if args.target_format in {"jpeg", "jpg"}
            else {f".{args.target_format}"}
        )
        source_exts = frozenset(IMAGE_EXTENSIONS - target_aliases)
    elif args.source_ext:
        source_exts = frozenset(_flatten_list_arg(args.source_ext, lowercase=True, ensure_dot=True))
    else:
        source_exts = DEFAULT_SOURCE_EXTS

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
        exclude_patterns=tuple(_flatten_list_arg(args.exclude)),
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
            f"  bytes written:    {result.bytes_written} "
            f"({_format_bytes(result.bytes_written)})"
        )
        if opts.archive_originals:
            archive_verb = "would archive" if opts.dry_run else "archived"
            ui.info(f"  files {archive_verb}: {result.files_archived}")
        if result.files_converted and not opts.dry_run:
            ui.success(f"  output: {opts.output_folder}")
        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors else EXIT_OK


def _cmd_info(args: argparse.Namespace, ui: UI) -> int:
    from dedupe.info import InfoOptions, run_info  # noqa: PLC0415

    opts = InfoOptions(
        source=args.folder,
        recursive=args.recursive,
        include_hidden=args.include_hidden,
        follow_symlinks=args.follow_symlinks,
        exclude_patterns=tuple(_flatten_list_arg(args.exclude)),
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
            f"({_format_bytes(result.total_size_bytes)})"
        )
        ui.info(
            f"  image size:      {result.image_size_bytes} "
            f"({_format_bytes(result.image_size_bytes)})"
        )

        if result.by_extension:
            ui.info("")
            ui.info("[bold]By extension[/bold] (count · size)")
            # Sort by count desc, then ext asc.
            for ext, count in sorted(result.by_extension.items(), key=lambda kv: (-kv[1], kv[0])):
                size = result.size_by_extension.get(ext, 0)
                ui.info(f"  {ext:<10} {count:>6}   {_format_bytes(size)}")

        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors else EXIT_OK


def _cmd_sweep(args: argparse.Namespace, ui: UI) -> int:
    from dedupe.sweep import SweepOptions, run_sweep  # noqa: PLC0415

    opts = SweepOptions(
        source=args.folder,
        sweep_junk=args.sweep_junk,
        quarantine_junk=args.quarantine_junk,
        junk_folder=args.junk_folder,
        log_folder=args.log_folder,
        dry_run=args.dry_run,
        recursive=args.recursive,
        follow_symlinks=args.follow_symlinks,
        exclude_patterns=tuple(_flatten_list_arg(args.exclude)),
    )

    try:
        result = run_sweep(opts, ui)
    except (FileNotFoundError, NotADirectoryError) as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    mode = "quarantine" if opts.quarantine_junk else "delete"

    if ui.config.json_mode:
        ui.emit_json(
            {
                "command": "sweep",
                "dry_run": opts.dry_run,
                "source": str(opts.source),
                "mode": mode,
                "sweep_junk": opts.sweep_junk,
                "junk_folder": str(opts.junk_folder) if opts.junk_folder else None,
                "log_folder": str(opts.log_folder) if opts.log_folder else None,
                "files_scanned": result.files_scanned,
                "files_swept": result.files_swept,
                "bytes_swept": result.bytes_swept,
                "errors": result.errors,
                "entries": [e.__dict__ for e in result.entries],
            }
        )
    else:
        verb = "would sweep" if opts.dry_run else ("moved" if opts.quarantine_junk else "deleted")
        ui.info("")
        ui.info("[bold]Summary[/bold]")
        ui.info(f"  files matched:  {result.files_scanned}")
        ui.info(f"  files {verb}: {result.files_swept}")
        ui.info(f"  bytes swept:    {result.bytes_swept} " f"({_format_bytes(result.bytes_swept)})")
        if result.files_swept and not opts.dry_run:
            if opts.quarantine_junk:
                target = opts.junk_folder or (opts.source.parent / f"{opts.source.name}-junk")
                ui.success(f"  quarantine: {target}")
            else:
                target = opts.log_folder or (opts.source.parent / f"{opts.source.name}-sweep-log")
                ui.success(f"  audit log:  {target}/sweep-manifest.json")
        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors else EXIT_OK


# --- entrypoint --------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_USAGE

    _setup_logging(getattr(args, "verbose", False), getattr(args, "quiet", False))
    ui = _make_ui(args)

    try:
        return args.func(args, ui)
    except KeyboardInterrupt:
        ui.error("interrupted")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
