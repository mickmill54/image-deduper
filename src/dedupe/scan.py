"""Scan a folder for byte-identical duplicate images and quarantine them.

Pipeline:
  1. Walk the source folder, collecting eligible image files.
  2. Hash each file (SHA-256, streamed) in a thread pool.
  3. Group files by hash. For each group with > 1 member, pick the keeper
     (shortest full path; alphabetical tiebreak) and move the rest to the
     dups folder, mirroring the original folder structure.
  4. Record each move in the manifest, flushing after every move.

Safety: this module never deletes files. All non-keepers are *moved* to
the dups folder. If a destination already exists, the move is skipped and
reported as a partial failure (exit code 3 from the caller).
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dedupe.manifest import MANIFEST_NAME, ManifestEntry, ManifestWriter
from dedupe.ui import UI

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
)

HASH_CHUNK_SIZE = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class ScanOptions:
    source: Path
    dups_folder: Path
    dry_run: bool = False
    recursive: bool = True
    threads: int = 0  # 0 = default (CPU count, capped)
    include_hidden: bool = False
    follow_symlinks: bool = False
    exclude_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanResult:
    files_scanned: int
    duplicate_groups: int
    files_moved: int
    bytes_reclaimed: int
    errors: list[str]
    moves: list[ManifestEntry]


def hash_file(path: Path) -> str:
    """Stream-hash a file with SHA-256."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def _is_hidden(path: Path, source: Path) -> bool:
    """A path is hidden if any component (relative to source) starts with '.'."""
    try:
        rel = path.relative_to(source)
    except ValueError:
        rel = path
    return any(part.startswith(".") for part in rel.parts)


def _matches_exclude(path: Path, source: Path, patterns: tuple[str, ...]) -> bool:
    """True if `path` matches any glob in `patterns` (relative to source).

    Each pattern is matched twice: once against the relative path
    (e.g. "exports/img.jpg") and once against the basename (e.g. "img.jpg").
    Either match excludes the file. Uses fnmatch semantics, which matches
    most users' intuition for shell-style globs (`*.tmp`, `exports/*`,
    `**/.DS_Store`).
    """
    if not patterns:
        return False
    import fnmatch  # noqa: PLC0415 — keep stdlib usage local

    try:
        rel = str(path.relative_to(source))
    except ValueError:
        rel = str(path)
    name = path.name
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat) for pat in patterns)


def iter_image_files(opts: ScanOptions) -> Iterator[Path]:
    """Yield candidate image paths under opts.source."""
    if not opts.source.exists():
        return
    if not opts.source.is_dir():
        return

    if opts.recursive:
        walker: Iterable[Path] = opts.source.rglob("*")
    else:
        walker = opts.source.iterdir()

    for path in walker:
        try:
            if path.is_symlink() and not opts.follow_symlinks:
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if not opts.include_hidden and _is_hidden(path, opts.source):
                continue
            if _matches_exclude(path, opts.source, opts.exclude_patterns):
                continue
        except OSError as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue
        yield path


def pick_keeper(paths: list[Path]) -> Path:
    """Shortest full-path string wins, alphabetical tiebreak."""
    return min(paths, key=lambda p: (len(str(p)), str(p)))


def _mirror_destination(original: Path, source: Path, dups_folder: Path) -> Path:
    """Map source/foo/bar.jpg -> dups_folder/foo/bar.jpg."""
    rel = original.resolve().relative_to(source.resolve())
    return dups_folder / rel


def _rel(path: Path, source: Path) -> str:
    """Render `path` relative to `source` for log output. Falls back to absolute."""
    try:
        return str(path.resolve().relative_to(source.resolve()))
    except ValueError:
        return str(path)


def _hash_all(files: list[Path], threads: int, ui: UI) -> tuple[dict[str, list[Path]], list[str]]:
    """Hash every file. Returns (hash -> [paths], errors)."""
    groups: dict[str, list[Path]] = {}
    errors: list[str] = []
    with (
        ui.progress("Hashing", total=len(files)) as progress,
        ThreadPoolExecutor(max_workers=threads or None) as pool,
    ):
        future_to_path = {pool.submit(hash_file, p): p for p in files}
        for fut in as_completed(future_to_path):
            path = future_to_path[fut]
            try:
                digest = fut.result()
            except OSError as exc:
                msg = f"hash failed for {path}: {exc}"
                errors.append(msg)
                ui.warn(msg)
                progress.advance(current=path.name)
                continue
            groups.setdefault(digest, []).append(path)
            progress.advance(current=path.name)
    return groups, errors


def _move_one(
    *,
    src: Path,
    dest: Path,
    dry_run: bool,
) -> None:
    """Move src to dest. Refuses to overwrite. Creates parent dirs."""
    if dest.exists():
        raise FileExistsError(f"destination already exists: {dest}")
    if dry_run:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def run_scan(opts: ScanOptions, ui: UI) -> ScanResult:  # noqa: PLR0912 — orchestrator with linear failure-mode branches
    """Execute a scan. Pure orchestration; per-step work lives in helpers."""
    if not opts.source.exists():
        raise FileNotFoundError(f"source folder does not exist: {opts.source}")
    if not opts.source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {opts.source}")

    # Discover files.
    ui.info(f"Scanning [bold]{opts.source}[/bold]")
    files = sorted(iter_image_files(opts))
    ui.detail(f"found {len(files)} candidate image file(s)")

    if not files:
        ui.info("no image files found")
        return ScanResult(
            files_scanned=0,
            duplicate_groups=0,
            files_moved=0,
            bytes_reclaimed=0,
            errors=[],
            moves=[],
        )

    # Hash in parallel.
    hash_groups, errors = _hash_all(files, opts.threads, ui)

    # Find duplicate groups.
    dup_groups = {h: paths for h, paths in hash_groups.items() if len(paths) > 1}
    if not dup_groups:
        ui.success("no duplicates found")
        return ScanResult(
            files_scanned=len(files),
            duplicate_groups=0,
            files_moved=0,
            bytes_reclaimed=0,
            errors=errors,
            moves=[],
        )

    # Set up manifest unless dry-run. If a pre-existing manifest exists in
    # the dups folder for this same source, resume from it.
    manifest_writer: ManifestWriter | None = None
    already_archived: set[str] = set()
    if not opts.dry_run:
        opts.dups_folder.mkdir(parents=True, exist_ok=True)
        manifest_path = opts.dups_folder / MANIFEST_NAME

        resume_from = None
        if manifest_path.is_file():
            try:
                from dedupe.manifest import load as _load_manifest  # noqa: PLC0415

                existing = _load_manifest(manifest_path)
            except (ValueError, OSError) as exc:
                ui.warn(
                    f"existing manifest at {manifest_path} could not be loaded "
                    f"({exc}); a fresh manifest will be written"
                )
            else:
                if existing.source_folder == str(opts.source.resolve()):
                    resume_from = existing
                    already_archived = {e.original_path for e in existing.entries}
                    ui.warn(
                        f"resuming from existing manifest with "
                        f"{len(already_archived)} entry(ies); already-archived "
                        f"originals will be skipped"
                    )
                else:
                    ui.warn(
                        f"existing manifest at {manifest_path} is from a "
                        f"different source folder ({existing.source_folder}); "
                        f"refusing to mix runs"
                    )
                    return ScanResult(
                        files_scanned=len(files),
                        duplicate_groups=0,
                        files_moved=0,
                        bytes_reclaimed=0,
                        errors=errors + [f"manifest source mismatch: {manifest_path}"],
                        moves=[],
                    )

        manifest_writer = ManifestWriter(
            path=manifest_path,
            source_folder=opts.source,
            dups_folder=opts.dups_folder,
            resume_from=resume_from,
        )

    moves: list[ManifestEntry] = []
    bytes_reclaimed = 0
    files_moved = 0

    for digest, paths in sorted(dup_groups.items()):
        keeper = pick_keeper(paths)
        losers = [p for p in paths if p != keeper and str(p) not in already_archived]
        if not losers:
            # Whole group already handled in a prior run.
            continue
        # Escape the literal '[' before the hash so rich doesn't try to
        # parse the hex string as a style tag (e.g. '[8a740dcc]'). Only
        # the opening bracket needs escaping; ']' on its own is harmless.
        ui.info(
            f"[cyan]\\[{digest[:12]}][/cyan] "
            f"{len(losers) + 1} files, keeping "
            f"[green]{_rel(keeper, opts.source)}[/green]"
        )
        for loser in losers:
            ui.info(
                f"  [dim]→[/dim] {'would move' if opts.dry_run else 'move'} "
                f"[yellow]{_rel(loser, opts.source)}[/yellow]"
            )
            entry = _process_loser(
                loser=loser,
                keeper=keeper,
                digest=digest,
                opts=opts,
                manifest_writer=manifest_writer,
                errors=errors,
                ui=ui,
            )
            if entry is None:
                continue
            files_moved += 1
            bytes_reclaimed += entry.size_bytes
            moves.append(entry)

    return ScanResult(
        files_scanned=len(files),
        duplicate_groups=len(dup_groups),
        files_moved=files_moved,
        bytes_reclaimed=bytes_reclaimed,
        errors=errors,
        moves=moves,
    )


def _process_loser(
    *,
    loser: Path,
    keeper: Path,
    digest: str,
    opts: ScanOptions,
    manifest_writer: ManifestWriter | None,
    errors: list[str],
    ui: UI,
) -> ManifestEntry | None:
    """Move one duplicate to quarantine and return its manifest entry.

    Returns None on failure (and appends a message to `errors`).
    """
    try:
        dest = _mirror_destination(loser, opts.source, opts.dups_folder)
    except ValueError as exc:
        errors.append(f"could not map path {loser}: {exc}")
        ui.error(errors[-1])
        return None

    try:
        size = loser.stat().st_size
    except OSError as exc:
        errors.append(f"stat failed for {loser}: {exc}")
        ui.error(errors[-1])
        return None

    try:
        _move_one(src=loser, dest=dest, dry_run=opts.dry_run)
    except FileExistsError as exc:
        errors.append(f"refusing to overwrite: {exc}")
        ui.error(errors[-1])
        return None
    except OSError as exc:
        errors.append(f"move failed for {loser} -> {dest}: {exc}")
        ui.error(errors[-1])
        return None

    ui.detail(
        f"    moved {_rel(loser, opts.source)} → " f"{_rel(dest, opts.dups_folder)} (in dups)"
    )

    if manifest_writer is not None:
        return manifest_writer.add(
            original_path=loser,
            new_path=dest,
            sha256=digest,
            kept_path=keeper,
            size_bytes=size,
        )
    # Dry-run: build an entry for reporting only.
    return ManifestEntry(
        original_path=str(loser),
        new_path=str(dest),
        sha256=digest,
        kept_path=str(keeper),
        size_bytes=size,
        timestamp=datetime.now(UTC).isoformat(),
    )
