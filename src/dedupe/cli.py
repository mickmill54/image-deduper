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


def _build_parser() -> argparse.ArgumentParser:
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
