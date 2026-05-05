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

import json
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from dedupe.scan import ScanOptions, iter_image_files
from dedupe.ui import UI

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
    files_converted: int = 0
    files_skipped: int = 0
    bytes_written: int = 0
    files_archived: int = 0
    errors: list[str] = field(default_factory=list)
    conversions: list[tuple[Path, Path]] = field(default_factory=list)
    archive_entries: list[ArchiveEntry] = field(default_factory=list)


class _ArchiveManifestWriter:
    """Incremental writer for the archive manifest. Atomic per-entry flush."""

    def __init__(
        self,
        path: Path,
        *,
        source_folder: Path,
        archive_folder: Path,
        output_folder: Path,
        target_format: str,
    ) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._payload: dict = {
            "version": ARCHIVE_MANIFEST_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "source_folder": str(source_folder.resolve()),
            "archive_folder": str(archive_folder.resolve()),
            "output_folder": str(output_folder.resolve()),
            "target_format": target_format,
            "entries": [],
        }
        self._flush()

    def add(self, entry: ArchiveEntry) -> None:
        with self._lock:
            self._payload["entries"].append(asdict(entry))
            self._flush()

    def _flush(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._payload, fh, indent=2, sort_keys=False)
            fh.write("\n")
            fh.flush()
        tmp.replace(self.path)


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
    )


def _mirror_destination(original: Path, source: Path, output_folder: Path, target_ext: str) -> Path:
    """source/foo/x.heic -> output_folder/foo/x.jpg (extension swap)."""
    rel = original.resolve().relative_to(source.resolve())
    return (output_folder / rel).with_suffix(target_ext)


def _archive_destination(original: Path, source: Path, archive_folder: Path) -> Path:
    """source/foo/x.heic -> archive_folder/foo/x.heic (extension preserved)."""
    rel = original.resolve().relative_to(source.resolve())
    return archive_folder / rel


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def _convert_one(
    *,
    src: Path,
    dest: Path,
    pillow_format: str,
    quality: int,
    dry_run: bool,
) -> int:
    """Convert one file. Returns bytes written (0 in dry-run)."""
    if dest.exists():
        raise FileExistsError(f"output already exists: {dest}")
    if dry_run:
        return 0

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


def run_convert(opts: ConvertOptions, ui: UI) -> ConvertResult:
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
            ): (src, dest)
            for src, dest in planned
        }
        for fut in as_completed(future_to_pair):
            src, dest = future_to_pair[fut]
            try:
                size = fut.result()
            except FileExistsError as exc:
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

            result.files_converted += 1
            result.bytes_written += size
            result.conversions.append((src, dest))
            verb = "would convert" if opts.dry_run else "converted"
            ui.info(
                f"  [dim]→[/dim] {verb} "
                f"[yellow]{_rel(src, opts.source)}[/yellow] → "
                f"[green]{_rel(dest, opts.output_folder)}[/green]"
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
                f"[yellow]{_rel(src, opts.source)}[/yellow] → "
                f"[green]{_rel(archive_dest, archive_folder)}[/green] (in archive)"
            )
        return

    archive_folder.mkdir(parents=True, exist_ok=True)
    manifest = _ArchiveManifestWriter(
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
        ui.detail(
            f"    archived {_rel(src, opts.source)} → " f"{_rel(archive_dest, archive_folder)}"
        )
