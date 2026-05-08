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
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dedupe.hash_cache import HashCache
from dedupe.manifest import MANIFEST_NAME, ManifestEntry, ManifestWriter
from dedupe.ui import UI
from dedupe.walk import (
    WalkOptions,
    is_hidden,
    matches_exclude,
    rel,
    walk_files,
)

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


# Backwards-compat shims for legacy imports. The shared implementations
# now live in `dedupe.walk`. Tests still import these names directly
# from `dedupe.scan`, so we keep the names here as thin re-exports.
_is_hidden = is_hidden
_matches_exclude = matches_exclude
_rel = rel


def iter_image_files(opts: ScanOptions) -> Iterator[Path]:
    """Yield candidate image paths under opts.source.

    Thin wrapper over `walk.walk_files` that adds the IMAGE_EXTENSIONS
    filter as the subcommand-specific predicate.
    """
    walk_opts = WalkOptions(
        source=opts.source,
        recursive=opts.recursive,
        follow_symlinks=opts.follow_symlinks,
        include_hidden=opts.include_hidden,
        exclude_patterns=opts.exclude_patterns,
    )
    yield from walk_files(walk_opts, predicate=lambda p: p.suffix.lower() in IMAGE_EXTENSIONS)


def pick_keeper(paths: list[Path]) -> Path:
    """Shortest full-path string wins, alphabetical tiebreak."""
    return min(paths, key=lambda p: (len(str(p)), str(p)))


def _mirror_destination(original: Path, source: Path, dups_folder: Path) -> Path:
    """Map source/foo/bar.jpg -> dups_folder/foo/bar.jpg."""
    rel_path = original.resolve().relative_to(source.resolve())
    return dups_folder / rel_path


def _hash_one_with_cache(path: Path, cache: HashCache | None) -> tuple[str, bool]:
    """Wrap :func:`hash_file` with a cache lookup-and-store cycle.

    Returns ``(digest, was_cache_hit)``. On a hit, the digest comes
    from the cache and no fresh SHA-256 runs. On a miss, the file is
    hashed and the result is written back to the cache so subsequent
    runs skip it.
    """
    if cache is not None:
        cached = cache.get(path)
        if cached is not None:
            return cached, True
    digest = hash_file(path)
    if cache is not None:
        cache.set(path, digest)
    return digest, False


def _hash_all(
    files: list[Path],
    threads: int,
    ui: UI,
    cache: HashCache | None = None,
) -> tuple[dict[str, list[Path]], list[str], int]:
    """Hash every file, optionally consulting/updating ``cache``.

    Returns ``(hash -> [paths], errors, cache_hits)``. ``cache_hits``
    counts the files whose digest came from the cache (no fresh
    SHA-256). Useful for the summary line so users see why their
    re-run was fast.
    """
    groups: dict[str, list[Path]] = {}
    errors: list[str] = []
    cache_hits = 0
    with (
        ui.progress("Hashing", total=len(files)) as progress,
        ThreadPoolExecutor(max_workers=threads or None) as pool,
    ):
        future_to_path = {pool.submit(_hash_one_with_cache, p, cache): p for p in files}
        for fut in as_completed(future_to_path):
            path = future_to_path[fut]
            try:
                digest, was_hit = fut.result()
            except OSError as exc:
                msg = f"hash failed for {path}: {exc}"
                errors.append(msg)
                ui.warn(msg)
                progress.advance(current=path.name)
                continue
            if was_hit:
                cache_hits += 1
            groups.setdefault(digest, []).append(path)
            progress.advance(current=path.name)
    return groups, errors, cache_hits


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


def run_scan(opts: ScanOptions, ui: UI) -> ScanResult:  # noqa: PLR0912, PLR0915 — orchestrator with linear failure-mode branches
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

    # Open the persistent hash cache so an interrupted run doesn't
    # re-hash everything on restart. Skipped on dry-run (no side
    # effects) and silently degraded to None if cache initialization
    # fails (the scan is still correct, just no faster than v0.11.0).
    hash_cache: HashCache | None = None
    if not opts.dry_run:
        try:
            hash_cache = HashCache.open(
                dups_folder=opts.dups_folder,
                source_folder=opts.source,
            )
            if len(hash_cache) > 0:
                ui.detail(f"hash cache: {len(hash_cache)} entry(ies) loaded")
        except OSError as exc:
            ui.warn(f"hash cache could not be opened ({exc}); proceeding without cache")
            hash_cache = None

    # Hash in parallel.
    hash_groups, errors, cache_hits = _hash_all(files, opts.threads, ui, cache=hash_cache)
    if hash_cache is not None and cache_hits > 0:
        ui.detail(f"hash cache: {cache_hits} hit(s), {len(files) - cache_hits} fresh hash(es)")

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
