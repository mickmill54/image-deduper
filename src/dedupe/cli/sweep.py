"""`dedupe sweep` subcommand: parser config + handler."""

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
        "sweep",
        help="Move/delete non-photo content (junk, non-images, videos) out of source folders",
        description=(
            "Walk a folder for non-photo content and clean it out. Three "
            "modes (combinable):\n"
            "  --junk        OS metadata files (Thumbs.db, .DS_Store, etc.). "
            "Default action: DELETE + log. --quarantine-junk to move instead.\n"
            "  --non-images  Arbitrary non-image files (txt, pdf, docx, etc.). "
            "Always moved (never deleted) to <folder>-non-images.\n"
            "  --videos      Video files (mov, mp4, m4v, avi, mkv, etc.). "
            "Always moved to '<folder> - videos' (mirrored subfolders inside "
            "gain a ' - videos' suffix so paths are self-documenting)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_global_flags(p)
    p.add_argument("folder", type=Path, help="Folder to sweep")

    # --- junk-files category ------------------------------------------------
    p.add_argument(
        "--junk",
        dest="sweep_junk",
        action="store_true",
        help=(
            "Sweep auto-generated OS metadata files (Thumbs.db, .DS_Store, "
            "desktop.ini, .AppleDouble). Default action: delete + log."
        ),
    )
    p.add_argument(
        "--quarantine-junk",
        action="store_true",
        help=(
            "Instead of deleting, MOVE junk files to a sibling folder "
            "(default: <folder>-junk) mirroring layout. Safer but creates a "
            "tree of nested empty-ish dirs."
        ),
    )
    p.add_argument(
        "--junk-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help=("Where to move junk files when --quarantine-junk is set " "(default: <folder>-junk)"),
    )
    p.add_argument(
        "--log-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Where to write the sweep manifest in junk-delete mode "
            "(default: <folder>-sweep-log). Ignored with --quarantine-junk "
            "(manifest goes alongside the quarantined files)."
        ),
    )

    # --- non-images category ------------------------------------------------
    p.add_argument(
        "--non-images",
        dest="sweep_non_images",
        action="store_true",
        help=(
            "Move arbitrary non-image files (.txt, .pdf, .docx, etc.) to a "
            "sibling folder. Always moves; never deletes (these are user "
            "content)."
        ),
    )
    p.add_argument(
        "--non-images-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help="Where to move non-image files (default: <folder>-non-images)",
    )

    # --- videos category ----------------------------------------------------
    p.add_argument(
        "--videos",
        dest="sweep_videos",
        action="store_true",
        help=(
            "Move video files (.mov, .mp4, .m4v, .avi, .mkv, etc.) to a "
            "sibling folder. Always moves; never deletes."
        ),
    )
    p.add_argument(
        "--videos-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help="Where to move video files (default: '<folder> - videos')",
    )

    # --- shared flags -------------------------------------------------------
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted/moved without changing anything",
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
    p.set_defaults(func=_cmd_sweep)


def _emit_summary(result, opts, ui: UI) -> None:
    """Print the rich-mode summary block. Extracted from `_cmd_sweep` to keep
    the handler's branch count low. Multi-category aware."""
    src = opts.source
    verb = "would sweep" if opts.dry_run else "swept"
    ui.info("")
    ui.info("[bold]Summary[/bold]")
    ui.info(f"  files matched:  {result.files_scanned}")
    ui.info(f"  files {verb}: {result.files_swept}")
    ui.info(f"  bytes swept:    {result.bytes_swept} " f"({format_bytes(result.bytes_swept)})")
    if opts.sweep_junk and result.junk_swept:
        ui.info(f"    junk:         {result.junk_swept}")
    if opts.sweep_non_images and result.non_images_swept:
        ui.info(f"    non-images:   {result.non_images_swept}")
    if opts.sweep_videos and result.videos_swept:
        ui.info(f"    videos:       {result.videos_swept}")

    if result.files_swept and not opts.dry_run:
        # Surface each category's destination so users know where to look.
        if opts.sweep_junk and result.junk_swept:
            if opts.quarantine_junk:
                target = opts.junk_folder or (src.parent / f"{src.name}-junk")
                ui.success(f"  junk:         {target}")
            else:
                target = opts.log_folder or (src.parent / f"{src.name}-sweep-log")
                ui.success(f"  junk log:     {target}/sweep-manifest.json")
        if opts.sweep_non_images and result.non_images_swept:
            target = opts.non_images_folder or (src.parent / f"{src.name}-non-images")
            ui.success(f"  non-images:   {target}")
        if opts.sweep_videos and result.videos_swept:
            target = opts.videos_folder or (src.parent / f"{src.name} - videos")
            ui.success(f"  videos:       {target}")
    if result.errors:
        ui.warn(f"completed with {len(result.errors)} error(s)")


def _cmd_sweep(args: argparse.Namespace, ui: UI) -> int:
    from dedupe.sweep import SweepOptions, run_sweep  # noqa: PLC0415

    # Resolve to absolute so `dedupe sweep .` lands destinations as
    # siblings of the source rather than inside it (see #43). Path(".")
    # has `.parent == Path(".")` and `.name == ""`, which collapses
    # `src.parent / f"{src.name} - videos"` into a relative path that
    # ends up inside the source folder.
    folder: Path = args.folder.resolve()

    opts = SweepOptions(
        source=folder,
        sweep_junk=args.sweep_junk,
        quarantine_junk=args.quarantine_junk,
        junk_folder=args.junk_folder,
        log_folder=args.log_folder,
        sweep_non_images=args.sweep_non_images,
        non_images_folder=args.non_images_folder,
        sweep_videos=args.sweep_videos,
        videos_folder=args.videos_folder,
        dry_run=args.dry_run,
        recursive=args.recursive,
        follow_symlinks=args.follow_symlinks,
        exclude_patterns=tuple(flatten_list_arg(args.exclude)),
    )

    try:
        result = run_sweep(opts, ui)
    except (FileNotFoundError, NotADirectoryError) as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    if ui.config.json_mode:
        ui.emit_json(
            {
                "command": "sweep",
                "dry_run": opts.dry_run,
                "source": str(opts.source),
                # junk-mode (the only category that can delete)
                "sweep_junk": opts.sweep_junk,
                "junk_mode": "quarantine" if opts.quarantine_junk else "delete",
                "junk_folder": str(opts.junk_folder) if opts.junk_folder else None,
                "log_folder": str(opts.log_folder) if opts.log_folder else None,
                # non-images + videos (always move)
                "sweep_non_images": opts.sweep_non_images,
                "non_images_folder": (
                    str(opts.non_images_folder) if opts.non_images_folder else None
                ),
                "sweep_videos": opts.sweep_videos,
                "videos_folder": (str(opts.videos_folder) if opts.videos_folder else None),
                # results: aggregate + per-category counts
                "files_scanned": result.files_scanned,
                "files_swept": result.files_swept,
                "junk_swept": result.junk_swept,
                "non_images_swept": result.non_images_swept,
                "videos_swept": result.videos_swept,
                "bytes_swept": result.bytes_swept,
                "errors": result.errors,
                "entries": [e.__dict__ for e in result.entries],
            }
        )
    else:
        _emit_summary(result, opts, ui)

    return EXIT_PARTIAL if result.errors else EXIT_OK
