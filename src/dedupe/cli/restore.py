"""`dedupe restore` subcommand: parser config + handler."""

from __future__ import annotations

import argparse
from pathlib import Path

from dedupe.cli.parser import EXIT_ERROR, EXIT_OK, EXIT_PARTIAL, add_global_flags
from dedupe.ui import UI


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "restore",
        help="Move quarantined files back to their original locations",
        description=(
            "Read the manifest in <dups-folder> and move every quarantined "
            "file back to its original location. Refuses to overwrite if a "
            "file already exists at the original path."
        ),
    )
    add_global_flags(p)
    p.add_argument("dups_folder", type=Path, help="Quarantine folder containing manifest.json")
    p.set_defaults(func=_cmd_restore)


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
