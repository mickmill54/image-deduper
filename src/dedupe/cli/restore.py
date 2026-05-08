"""`dedupe restore` subcommand: parser config + handler."""

from __future__ import annotations

import argparse
from pathlib import Path

from dedupe.cli.parser import EXIT_ERROR, EXIT_OK, EXIT_PARTIAL, add_global_flags
from dedupe.ui import UI


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "restore",
        help="Undo a previous scan or sweep by replaying its manifest",
        description=(
            "Read the manifest in <folder> and reverse the operation. "
            "Auto-detects manifest type:\n"
            "  manifest.json       → scan dups folder (move dups back)\n"
            "  sweep-manifest.json → sweep output folder (videos / non-images:\n"
            "                        reverse moves; junk-log: report deletes)\n"
            "Refuses to overwrite if a file already exists at the original path."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_global_flags(p)
    p.add_argument(
        "dups_folder",
        type=Path,
        metavar="folder",
        help="Folder containing manifest.json or sweep-manifest.json",
    )
    p.set_defaults(func=_cmd_restore)


def _cmd_restore(args: argparse.Namespace, ui: UI) -> int:
    # Lazy import: keeps non-restore paths from pulling restore module symbols.
    from dedupe.restore import RestoreOptions, run_restore  # noqa: PLC0415

    # Resolve to absolute so `dedupe restore .` behaves like the other
    # subcommands and avoids surprising relative-path interactions
    # (mirrors the v0.10.1 fix in #43).
    folder = args.dups_folder.resolve()
    opts = RestoreOptions(dups_folder=folder)
    try:
        result = run_restore(opts, ui)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        ui.error(str(exc))
        return EXIT_ERROR

    if ui.config.json_mode:
        ui.emit_json(
            {
                "command": "restore",
                "folder": str(opts.dups_folder),
                "manifest_kind": result.manifest_kind,
                "files_restored": result.files_restored,
                "files_skipped": result.files_skipped,
                "deleted_entries": result.deleted_entries,
                "conflicts": result.conflicts,
                "errors": result.errors,
            }
        )
    else:
        ui.info("")
        ui.info("[bold]Summary[/bold]")
        ui.info(f"  manifest:       {result.manifest_kind}")
        ui.info(f"  files restored: {result.files_restored}")
        ui.info(f"  files skipped:  {result.files_skipped}")
        if result.deleted_entries:
            ui.info(f"  deleted (one-way): {result.deleted_entries}")
        if result.conflicts:
            ui.warn(f"  {len(result.conflicts)} conflict(s) — see above")
        if result.errors:
            ui.warn(f"completed with {len(result.errors)} error(s)")

    return EXIT_PARTIAL if result.errors or result.conflicts else EXIT_OK
