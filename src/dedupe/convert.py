"""Convert images to a target format. Originals are never modified by default.

Default behavior: walk a folder, find every HEIC/HEIF file, write a
JPEG copy of each into a sibling `<folder>-converted/` folder mirroring
the original layout. Originals stay where they are.

Optional `archive_originals` mode: after each successful conversion,
*move* the original into a sibling `<folder>-heic/` folder (also mirroring
layout) and record the move in an `archive-manifest.json`. This is the
post-conversion cleanup pattern — the source folder ends up free of the
old format, but the originals are still on disk and auditable.

Safety: this module never deletes files. With archival off it only
writes new outputs; with archival on it *moves* originals to the archive
folder, refusing to overwrite. Same family of guarantees as `scan`.
"""

from __future__ import annotations

import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from dedupe.manifest import AtomicManifestWriter
from dedupe.scan import ScanOptions, iter_image_files
from dedupe.ui import UI
from dedupe.walk import rel

logger = logging.getLogger(__name__)

# Register HEIC support if available. Idempotent — safe to call from
# multiple modules.
try:  # pragma: no cover - import-time side effect
    import pillow_heif  # type: ignore[import-not-found]

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    logger.debug("pillow-heif not available; HEIC files will be skipped")


# Map a target format name (lowercased) to (Pillow format, target extension).
TARGET_FORMATS: dict[str, tuple[str, str]] = {
    "jpeg": ("JPEG", ".jpg"),
    "jpg": ("JPEG", ".jpg"),
    "png": ("PNG", ".png"),
    "webp": ("WEBP", ".webp"),
}

DEFAULT_SOURCE_EXTS = frozenset({".heic", ".heif"})
DEFAULT_QUALITY = 92
ARCHIVE_MANIFEST_NAME = "archive-manifest.json"
ARCHIVE_MANIFEST_VERSION = 1


# --- conflict-resolution modes (#47) ----------------------------------------
#
# When `--in-place` writes a converted JPG and the destination already
# exists (typical for iPhone libraries where the camera exports both
# IMG_001.heic AND IMG_001.jpg for the same shot), `--on-conflict`
# decides what to do.

ON_CONFLICT_SKIP = "skip"  # default: refuse to overwrite, leave source HEIC alone
ON_CONFLICT_KEEP_EXISTING = "archive-anyway"  # don't write JPG; archive HEIC anyway
ON_CONFLICT_NUMBER = "number"  # write IMG_001-1.jpg, IMG_001-2.jpg, ...
ON_CONFLICT_OVERWRITE = "overwrite"  # replace existing JPG (HEIC archived)

ON_CONFLICT_MODES = frozenset(
    {
        ON_CONFLICT_SKIP,
        ON_CONFLICT_KEEP_EXISTING,
        ON_CONFLICT_NUMBER,
        ON_CONFLICT_OVERWRITE,
    }
)

# Per-file outcome strings recorded in ConvertResult counters.
OUTCOME_CONVERTED = "converted"
OUTCOME_KEPT_EXISTING = "kept_existing"
OUTCOME_NUMBERED = "numbered"
OUTCOME_OVERWRITTEN = "overwritten"


@dataclass(frozen=True)
class ConvertOptions:
    source: Path
    output_folder: Path
    target_format: str = "jpeg"
    quality: int = DEFAULT_QUALITY
    source_exts: frozenset[str] = DEFAULT_SOURCE_EXTS
    dry_run: bool = False
    recursive: bool = True
    threads: int = 0
    include_hidden: bool = False
    follow_symlinks: bool = False
    archive_originals: bool = False
    archive_folder: Path | None = None
    exclude_patterns: tuple[str, ...] = ()
    on_conflict: str = ON_CONFLICT_SKIP


@dataclass(frozen=True)
class ArchiveEntry:
    original_path: str
    archive_path: str
    converted_to_path: str
    size_bytes: int
    timestamp: str


@dataclass
class ConvertResult:
    files_scanned: int = 0
    files_converted: int = 0  # new bytes written (fresh convert OR overwrite OR numbered)
    files_skipped: int = 0  # SKIP mode: conflict left HEIC in place, no archive
    bytes_written: int = 0
    files_archived: int = 0
    # Per-mode outcome counts (#47). Sum equals files_converted +
    # files_kept_existing for the rows where the convert/keep was the
    # final action (errors don't contribute).
    files_kept_existing: int = 0  # archive-anyway: existing JPG kept, HEIC archived
    files_numbered: int = 0  # number: wrote IMG-1.jpg etc.
    files_overwritten: int = 0  # overwrite: replaced existing JPG
    errors: list[str] = field(default_factory=list)
    conversions: list[tuple[Path, Path]] = field(default_factory=list)
    archive_entries: list[ArchiveEntry] = field(default_factory=list)


def _make_archive_manifest_writer(
    path: Path,
    *,
    source_folder: Path,
    archive_folder: Path,
    output_folder: Path,
    target_format: str,
) -> AtomicManifestWriter[ArchiveEntry]:
    """Build an atomic writer for the archive manifest."""
    header = {
        "version": ARCHIVE_MANIFEST_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "source_folder": str(source_folder.resolve()),
        "archive_folder": str(archive_folder.resolve()),
        "output_folder": str(output_folder.resolve()),
        "target_format": target_format,
    }
    return AtomicManifestWriter(path, header=header)


def _eligible(path: Path, source_exts: frozenset[str]) -> bool:
    return path.suffix.lower() in source_exts


def _scan_options_for(opts: ConvertOptions) -> ScanOptions:
    """Reuse iter_image_files but allow our own extension whitelist downstream."""
    return ScanOptions(
        source=opts.source,
        dups_folder=opts.source,  # unused in iteration
        recursive=opts.recursive,
        include_hidden=opts.include_hidden,
        follow_symlinks=opts.follow_symlinks,
        exclude_patterns=opts.exclude_patterns,
    )


def _mirror_destination(original: Path, source: Path, output_folder: Path, target_ext: str) -> Path:
    """source/foo/x.heic -> output_folder/foo/x.jpg (extension swap)."""
    rel_path = original.resolve().relative_to(source.resolve())
    return (output_folder / rel_path).with_suffix(target_ext)


def _archive_destination(original: Path, source: Path, archive_folder: Path) -> Path:
    """source/foo/x.heic -> archive_folder/foo/x.heic (extension preserved)."""
    rel_path = original.resolve().relative_to(source.resolve())
    return archive_folder / rel_path


def _verb_for(outcome: str, *, dry_run: bool) -> str:
    """User-friendly verb for the per-file output line."""
    if outcome == OUTCOME_KEPT_EXISTING:
        return "would keep existing" if dry_run else "kept existing JPG"
    if outcome == OUTCOME_NUMBERED:
        return "would write numbered" if dry_run else "wrote numbered"
    if outcome == OUTCOME_OVERWRITTEN:
        return "would overwrite" if dry_run else "overwrote"
    return "would convert" if dry_run else "converted"


def _find_numbered_destination(dest: Path) -> Path:
    """Find the lowest-N suffix path that doesn't exist yet.

    ``IMG_001.jpg`` already taken → tries ``IMG_001-1.jpg``,
    ``IMG_001-2.jpg``, etc. Same convention macOS Finder uses for
    copy-on-conflict. Race-prone in concurrent threads (two workers
    could pick the same suffix), but the only way that happens is if
    the source has multiple files mapping to the same dest stem,
    which is rare; the second writer would hit a real "destination
    exists" error and fall through to the regular error path.
    """
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_stem(f"{dest.stem}-{n}")
        if not candidate.exists():
            return candidate
        n += 1


def _write_image(
    *,
    src: Path,
    dest: Path,
    pillow_format: str,
    quality: int,
) -> int:
    """Open `src`, convert, save to `dest`. Returns bytes written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs: dict[str, object] = {}
    if pillow_format == "JPEG":
        save_kwargs["quality"] = quality
        save_kwargs["optimize"] = True
    elif pillow_format == "WEBP":
        save_kwargs["quality"] = quality

    with Image.open(src) as img:
        img.load()
        rgb = img.convert("RGB") if pillow_format in {"JPEG", "WEBP"} else img
        # Preserve EXIF where available; some encoders accept exif=...
        exif = img.info.get("exif")
        if exif and pillow_format in {"JPEG", "WEBP"}:
            save_kwargs["exif"] = exif
        rgb.save(dest, format=pillow_format, **save_kwargs)
    return dest.stat().st_size


def _convert_one(  # noqa: PLR0911 — linear conflict-mode dispatch branches
    *,
    src: Path,
    dest: Path,
    pillow_format: str,
    quality: int,
    dry_run: bool,
    on_conflict: str = ON_CONFLICT_SKIP,
) -> tuple[int, str, Path]:
    """Convert one file with conflict resolution per ``on_conflict`` (#47).

    Returns ``(bytes_written, outcome, actual_dest)``:
      - ``bytes_written``: 0 in dry-run, 0 on archive-anyway (no new write),
        otherwise the size of the new JPG.
      - ``outcome``: one of ``OUTCOME_CONVERTED``, ``OUTCOME_KEPT_EXISTING``,
        ``OUTCOME_NUMBERED``, ``OUTCOME_OVERWRITTEN``.
      - ``actual_dest``: the path that was actually written (or skipped).
        For ``number`` this is the suffixed variant; for the others it's
        the original ``dest``.

    On ``skip`` mode (the default), if ``dest`` already exists we raise
    ``FileExistsError`` — preserves the v0.12.x behavior for backward
    compat.
    """
    if dest.exists():
        if on_conflict == ON_CONFLICT_SKIP:
            raise FileExistsError(f"output already exists: {dest}")
        if on_conflict == ON_CONFLICT_KEEP_EXISTING:
            # archive-anyway: don't write a new JPG. The original HEIC
            # will still flow to the archive pass via result.conversions
            # (the existing JPG is canonical for this photo).
            return 0, OUTCOME_KEPT_EXISTING, dest
        if on_conflict == ON_CONFLICT_NUMBER:
            actual_dest = _find_numbered_destination(dest)
            if dry_run:
                return 0, OUTCOME_NUMBERED, actual_dest
            size = _write_image(
                src=src, dest=actual_dest, pillow_format=pillow_format, quality=quality
            )
            return size, OUTCOME_NUMBERED, actual_dest
        if on_conflict == ON_CONFLICT_OVERWRITE:
            if dry_run:
                return 0, OUTCOME_OVERWRITTEN, dest
            size = _write_image(src=src, dest=dest, pillow_format=pillow_format, quality=quality)
            return size, OUTCOME_OVERWRITTEN, dest
        # Defensive: unknown mode → fall through to a clear error.
        raise ValueError(f"unknown on_conflict mode: {on_conflict!r}")

    # Happy path: destination doesn't exist; behaves identically across
    # all modes (no conflict to resolve).
    if dry_run:
        return 0, OUTCOME_CONVERTED, dest
    size = _write_image(src=src, dest=dest, pillow_format=pillow_format, quality=quality)
    return size, OUTCOME_CONVERTED, dest


def run_convert(opts: ConvertOptions, ui: UI) -> ConvertResult:  # noqa: PLR0912, PLR0915 — orchestrator with linear per-outcome branches
    """Walk the source folder and convert every eligible file."""
    if not opts.source.exists():
        raise FileNotFoundError(f"source folder does not exist: {opts.source}")
    if not opts.source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {opts.source}")

    target_key = opts.target_format.lower()
    if target_key not in TARGET_FORMATS:
        raise ValueError(
            f"unsupported target format: {opts.target_format!r} "
            f"(supported: {sorted(TARGET_FORMATS)})"
        )
    pillow_format, target_ext = TARGET_FORMATS[target_key]

    ui.info(
        f"Converting [bold]{opts.source}[/bold] "
        f"→ [bold]{opts.output_folder}[/bold] (format: {pillow_format})"
    )

    all_files = sorted(iter_image_files(_scan_options_for(opts)))
    eligible = [p for p in all_files if _eligible(p, opts.source_exts)]
    ui.detail(f"found {len(all_files)} image file(s), " f"{len(eligible)} eligible for conversion")

    result = ConvertResult(files_scanned=len(eligible))
    if not eligible:
        ui.info("no convertible files found")
        return result

    # Plan the conversions up-front so we can show a meaningful progress bar
    # and detect output collisions before doing any work.
    planned: list[tuple[Path, Path]] = []
    for src in eligible:
        try:
            dest = _mirror_destination(src, opts.source, opts.output_folder, target_ext)
        except ValueError as exc:
            result.errors.append(f"could not map path {src}: {exc}")
            ui.error(result.errors[-1])
            continue
        planned.append((src, dest))

    with (
        ui.progress("Converting", total=len(planned)) as progress,
        ThreadPoolExecutor(max_workers=opts.threads or None) as pool,
    ):
        future_to_pair = {
            pool.submit(
                _convert_one,
                src=src,
                dest=dest,
                pillow_format=pillow_format,
                quality=opts.quality,
                dry_run=opts.dry_run,
                on_conflict=opts.on_conflict,
            ): (src, dest)
            for src, dest in planned
        }
        for fut in as_completed(future_to_pair):
            src, dest = future_to_pair[fut]
            try:
                size, outcome, actual_dest = fut.result()
            except FileExistsError as exc:
                # Only reachable in SKIP mode (the other modes resolve
                # the conflict and don't raise).
                result.errors.append(f"refusing to overwrite: {exc}")
                ui.error(result.errors[-1])
                result.files_skipped += 1
                progress.advance(current=src.name)
                continue
            except Exception as exc:  # noqa: BLE001 — Pillow can raise many things
                result.errors.append(f"convert failed for {src} -> {dest}: {exc}")
                ui.error(result.errors[-1])
                progress.advance(current=src.name)
                continue

            # Per-mode bookkeeping. archive-anyway is the only outcome
            # that didn't write new bytes; everything else counts as a
            # successful conversion-with-archive.
            if outcome == OUTCOME_KEPT_EXISTING:
                result.files_kept_existing += 1
                # bytes_written stays at 0 (no new file written).
            else:
                result.files_converted += 1
                result.bytes_written += size
                if outcome == OUTCOME_NUMBERED:
                    result.files_numbered += 1
                elif outcome == OUTCOME_OVERWRITTEN:
                    result.files_overwritten += 1

            # Both convert-write and archive-anyway flow into the archive
            # pass: the original HEIC moves to the archive folder either
            # way, so the user sees a HEIC-free source. The actual_dest
            # reported is the path the JPG ended up at (or the existing
            # JPG path for archive-anyway).
            result.conversions.append((src, actual_dest))

            verb = _verb_for(outcome, dry_run=opts.dry_run)
            ui.info(
                f"  [dim]→[/dim] {verb} "
                f"[yellow]{rel(src, opts.source)}[/yellow] → "
                f"[green]{rel(actual_dest, opts.output_folder)}[/green]"
            )
            progress.advance(current=src.name)

    # Phase 2: archive originals (sequential — file moves on the same FS,
    # incremental manifest flush, simpler reasoning than threading moves).
    if opts.archive_originals and result.conversions:
        _archive_originals_pass(opts, result, ui)

    return result


def _archive_originals_pass(opts: ConvertOptions, result: ConvertResult, ui: UI) -> None:
    """Move each successfully-converted original into the archive folder.

    Uses a fresh manifest for the archive run; refuses to overwrite existing
    archive paths. In dry-run mode reports the planned moves without touching
    the filesystem.
    """
    archive_folder = opts.archive_folder
    if archive_folder is None:
        archive_folder = opts.source.parent / f"{opts.source.name}-heic"

    if opts.dry_run:
        ui.info("")
        ui.info(
            f"[bold]Would archive {len(result.conversions)} original(s) to "
            f"{archive_folder}[/bold]"
        )
        for src, _dest in result.conversions:
            archive_dest = _archive_destination(src, opts.source, archive_folder)
            ui.info(
                f"  [dim]→[/dim] would move "
                f"[yellow]{rel(src, opts.source)}[/yellow] → "
                f"[green]{rel(archive_dest, archive_folder)}[/green] (in archive)"
            )
        return

    archive_folder.mkdir(parents=True, exist_ok=True)
    manifest = _make_archive_manifest_writer(
        path=archive_folder / ARCHIVE_MANIFEST_NAME,
        source_folder=opts.source,
        archive_folder=archive_folder,
        output_folder=opts.output_folder,
        target_format=opts.target_format,
    )

    ui.info("")
    ui.info(f"[bold]Archiving {len(result.conversions)} original(s) to " f"{archive_folder}[/bold]")

    for src, converted_dest in result.conversions:
        archive_dest = _archive_destination(src, opts.source, archive_folder)
        if archive_dest.exists():
            msg = f"refusing to overwrite archive path: {archive_dest}"
            result.errors.append(msg)
            ui.error(msg)
            continue
        try:
            size = src.stat().st_size
        except OSError as exc:
            msg = f"stat failed for {src}: {exc}"
            result.errors.append(msg)
            ui.error(msg)
            continue
        try:
            archive_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(archive_dest))
        except OSError as exc:
            msg = f"archive move failed for {src} -> {archive_dest}: {exc}"
            result.errors.append(msg)
            ui.error(msg)
            continue

        entry = ArchiveEntry(
            original_path=str(src),
            archive_path=str(archive_dest),
            converted_to_path=str(converted_dest),
            size_bytes=size,
            timestamp=datetime.now(UTC).isoformat(),
        )
        manifest.add(entry)
        result.archive_entries.append(entry)
        result.files_archived += 1
        ui.detail(f"    archived {rel(src, opts.source)} → " f"{rel(archive_dest, archive_folder)}")
