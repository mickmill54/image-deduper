"""Replay a manifest: move every quarantined file back to its original path.

Refuses to overwrite. If a file already exists at the original location,
that entry is skipped and reported as a conflict.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dedupe import manifest as manifest_mod
from dedupe.ui import UI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RestoreOptions:
    dups_folder: Path


@dataclass
class RestoreResult:
    files_restored: int = 0
    files_skipped: int = 0
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_restore(opts: RestoreOptions, ui: UI) -> RestoreResult:
    if not opts.dups_folder.exists():
        raise FileNotFoundError(f"dups folder does not exist: {opts.dups_folder}")
    if not opts.dups_folder.is_dir():
        raise NotADirectoryError(f"dups path is not a directory: {opts.dups_folder}")

    manifest_path = opts.dups_folder / manifest_mod.MANIFEST_NAME
    manifest = manifest_mod.load(manifest_path)
    ui.info(f"Restoring [bold]{len(manifest.entries)}[/bold] file(s) from {manifest_path}")

    result = RestoreResult()

    with ui.progress("Restoring", total=len(manifest.entries)) as progress:
        for entry in manifest.entries:
            original = Path(entry.original_path)
            current = Path(entry.new_path)

            if not current.exists():
                msg = f"missing in dups folder: {current}"
                result.errors.append(msg)
                ui.error(msg)
                progress.advance(current=current.name)
                continue

            if original.exists():
                msg = f"original location occupied, skipping: {original}"
                result.conflicts.append(str(original))
                result.files_skipped += 1
                ui.warn(msg)
                progress.advance(current=current.name)
                continue

            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(original))
            except OSError as exc:
                msg = f"restore failed for {current} -> {original}: {exc}"
                result.errors.append(msg)
                ui.error(msg)
                progress.advance(current=current.name)
                continue

            result.files_restored += 1
            ui.detail(f"  restored {current} -> {original}")
            progress.advance(current=current.name)

    if result.files_restored:
        ui.success(f"restored {result.files_restored} file(s)")
    return result
