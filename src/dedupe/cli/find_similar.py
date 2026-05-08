"""`dedupe find-similar` subcommand: parser config + handler."""

from __future__ import annotations

import argparse
from pathlib import Path

from dedupe.cli.parser import EXIT_ERROR, EXIT_OK, EXIT_PARTIAL, add_global_flags
from dedupe.ui import UI


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "find-similar",
        help="Report visually-similar photos (read-only, no moves)",
        description=(
            "Find visually-similar (but not byte-identical) photos using "
            "perceptual hashing. Report only — never moves files. Outputs "
            "a self-contained HTML report with side-by-side thumbnails."
        ),
    )
    add_global_flags(p)
    p.add_argument("folder", type=Path, help="Folder to scan")
    p.add_argument(
        "--threshold",
        type=int,
        default=5,
        metavar="N",
        help="Hamming distance threshold (default: 5; lower = stricter)",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=Path("similar-report.html"),
        metavar="PATH",
        help="HTML report output path (default: similar-report.html in cwd)",
    )
    p.set_defaults(func=_cmd_find_similar)


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
