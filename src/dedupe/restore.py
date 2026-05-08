"""Replay a manifest: undo a previous `scan` or `sweep` operation.

`dedupe restore <folder>` auto-detects the manifest type from the
filenames present in the folder:

  - ``manifest.json``       → scan dups (move each quarantined file
                              back to its original location).
  - ``sweep-manifest.json`` → sweep output. Each entry is either:
                                * ``action="moved"`` → reverse the move
                                  (e.g. ``--non-images`` or ``--videos``
                                  quarantine).
                                * ``action="deleted"`` → counted but not
                                  restorable (``--junk`` deletes are
                                  intentionally one-way; the targeted
                                  files are auto-regenerated OS metadata
                                  like ``.DS_Store`` / ``Thumbs.db``).

Both paths share the refuse-to-overwrite contract: if a file already
exists at the original location, that entry is reported as a conflict
and skipped, so a partial restore never destroys live data.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dedupe import manifest as manifest_mod
from dedupe.sweep import (
    ACTION_DELETED,
    ACTION_MOVED,
    SWEEP_MANIFEST_NAME,
    SWEEP_MANIFEST_VERSION,
)
from dedupe.ui import UI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RestoreOptions:
    # Historically named ``dups_folder`` for the scan-only path; with
    # sweep support added it's overloaded to mean "any folder containing
    # a recognized manifest file". Kept as ``dups_folder`` for backward
    # compatibility with the existing CLI handler and tests.
    dups_folder: Path


@dataclass
class RestoreResult:
    files_restored: int = 0
    files_skipped: int = 0
    # Sweep-specific: deleted entries are counted but not restorable.
    # Always 0 for scan restores.
    deleted_entries: int = 0
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Which manifest path drove this restore (set by the dispatcher).
    # Useful for the CLI summary.
    manifest_kind: str = ""  # "scan" or "sweep"


def run_restore(opts: RestoreOptions, ui: UI) -> RestoreResult:
    """Auto-detect manifest type and dispatch to the right restore path."""
    folder = opts.dups_folder
    if not folder.exists():
        raise FileNotFoundError(f"folder does not exist: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"path is not a directory: {folder}")

    scan_manifest = folder / manifest_mod.MANIFEST_NAME
    sweep_manifest = folder / SWEEP_MANIFEST_NAME

    has_scan = scan_manifest.is_file()
    has_sweep = sweep_manifest.is_file()

    if has_scan and has_sweep:
        # Defensive: in normal use a folder is owned by exactly one
        # subcommand, so this should never happen. If it does, refuse
        # rather than silently picking one and surprising the user.
        raise ValueError(
            f"both scan and sweep manifests present in {folder}; "
            f"please disambiguate by removing whichever you don't want "
            f"to restore from"
        )
    if has_scan:
        return _run_scan_restore(folder, scan_manifest, ui)
    if has_sweep:
        return _run_sweep_restore(folder, sweep_manifest, ui)

    raise FileNotFoundError(
        f"no manifest found in {folder} "
        f"(expected {manifest_mod.MANIFEST_NAME} or {SWEEP_MANIFEST_NAME})"
    )


# --- scan restore (original behavior, lifted into a private helper) ---------


def _run_scan_restore(folder: Path, manifest_path: Path, ui: UI) -> RestoreResult:
    manifest = manifest_mod.load(manifest_path)
    ui.info(f"Restoring [bold]{len(manifest.entries)}[/bold] file(s) from {manifest_path}")

    result = RestoreResult(manifest_kind="scan")

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


# --- sweep restore (new) ----------------------------------------------------


def _load_sweep_manifest(manifest_path: Path) -> dict:
    """Read and lightly validate a sweep manifest. Returns the raw dict.

    We don't reuse ``manifest_mod.load`` because that loader is hard-wired
    to the scan-shaped ``ManifestEntry``. Sweep entries have a different
    schema (``action``, ``new_path`` may be null for deletes) and we want
    to keep the two loaders separate so a schema bump on one side doesn't
    silently break the other.
    """
    with manifest_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"sweep manifest is not a JSON object: {manifest_path}")
    version = data.get("version")
    if version != SWEEP_MANIFEST_VERSION:
        raise ValueError(
            f"unsupported sweep manifest version: {version!r} "
            f"(expected {SWEEP_MANIFEST_VERSION})"
        )
    if not isinstance(data.get("entries", []), list):
        raise ValueError(f"sweep manifest 'entries' is not a list: {manifest_path}")
    return data


def _run_sweep_restore(  # noqa: PLR0915 — linear per-entry failure branches
    folder: Path, manifest_path: Path, ui: UI
) -> RestoreResult:
    data = _load_sweep_manifest(manifest_path)
    entries = data["entries"]
    category = data.get("category", "?")
    mode = data.get("mode", "?")

    ui.info(
        f"Restoring sweep manifest [bold]{category}[/bold] (mode={mode}) "
        f"with {len(entries)} entry(ies) from {manifest_path}"
    )

    result = RestoreResult(manifest_kind="sweep")

    with ui.progress("Restoring", total=len(entries)) as progress:
        for entry in entries:
            action = entry.get("action")
            original = Path(entry["original_path"])

            if action == ACTION_DELETED:
                # Junk-delete entries are intentionally one-way. Count
                # them so the summary line is accurate, but don't try
                # to recreate metadata files the OS regenerates anyway.
                result.deleted_entries += 1
                progress.advance(current=original.name)
                continue

            if action != ACTION_MOVED:
                msg = f"unknown action {action!r} in entry for {original}; skipping"
                result.errors.append(msg)
                ui.error(msg)
                progress.advance(current=original.name)
                continue

            new_path_str = entry.get("new_path")
            if new_path_str is None:
                msg = f"moved entry has no new_path: {original}"
                result.errors.append(msg)
                ui.error(msg)
                progress.advance(current=original.name)
                continue
            current = Path(new_path_str)

            if not current.exists():
                msg = f"missing in sweep folder: {current}"
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
    if result.deleted_entries:
        ui.info(
            f"{result.deleted_entries} entry(ies) had action=deleted "
            f"(junk auto-regenerates; nothing to restore)"
        )
    return result
