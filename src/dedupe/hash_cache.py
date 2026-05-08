"""Persistent SHA-256 hash cache so an interrupted scan doesn't re-hash on restart.

Hashing dominates `dedupe scan`'s wall-clock time on large libraries
(SHA-256 over hundreds of GB). Without a cache, a Ctrl-C or crash 80%
of the way through means losing 80% of the hash work. The previous
resume path only skipped already-*moved* files, not already-*hashed*
ones — so the slow part still ran every time.

This module provides a side-car cache living in the dups folder
(``.hash-cache.jsonl``). Format is append-only JSONL so a per-file
flush is O(1) — important when caching tens of thousands of entries.

Layout::

    {"_header": {"version": 1, "source_folder": "<abs-path>", "created_at": "..."}}
    {"path": "<abs>", "mtime_ns": 12345, "size": 67890, "sha256": "deadbeef..."}
    {"path": "<abs>", "mtime_ns": ..., "size": ..., "sha256": "..."}
    ...

Reads accumulate the latest entry per path (later lines override
earlier ones for the same key). Cache hits require an *exact* match on
``(mtime_ns, size)`` — any mismatch falls through to a fresh hash.

Concurrency: the writer is thread-safe. Multiple `ThreadPoolExecutor`
workers can call `set()` concurrently; the lock serializes the
append. The cost is a single flock-like lock around the file write,
which is dwarfed by the SHA-256 cost it saves.

Failure modes:
- Missing cache file → empty cache, scan hashes everything (no-op).
- Corrupt header / version mismatch → cache discarded, scan rebuilds
  from scratch (logged as a warning).
- Source-folder mismatch → cache discarded (the cache was written for
  a different source; reusing it would be wrong).
- A single corrupt entry line → that line is skipped, the rest of the
  cache loads.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HASH_CACHE_NAME = ".hash-cache.jsonl"
HASH_CACHE_VERSION = 1


class HashCache:
    """Append-only SHA-256 cache keyed by ``(path, mtime_ns, size)``.

    Construct via :meth:`load` or :meth:`open`; never instantiate
    directly. ``load`` is read-only (used for inspection / tests);
    ``open`` returns a writable cache that flushes per ``set()``.
    """

    def __init__(
        self,
        *,
        path: Path,
        source_folder: Path,
        entries: dict[str, tuple[int, int, str]],
        writable: bool,
    ) -> None:
        self._path = path
        self._source_folder = source_folder.resolve()
        self._entries = entries  # path-str -> (mtime_ns, size, sha256)
        self._writable = writable
        self._lock = threading.Lock()

    # --- read API -------------------------------------------------------

    def get(self, path: Path) -> str | None:
        """Return the cached digest for ``path`` if mtime+size still match.

        Returns ``None`` on cache miss (path not cached, or stat changed).
        Re-stats the file on each call — cheap relative to SHA-256.
        """
        cached = self._entries.get(str(path))
        if cached is None:
            return None
        cached_mtime, cached_size, digest = cached
        try:
            st = path.stat()
        except OSError:
            return None
        if st.st_mtime_ns != cached_mtime or st.st_size != cached_size:
            return None
        return digest

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def source_folder(self) -> Path:
        return self._source_folder

    # --- write API ------------------------------------------------------

    def set(self, path: Path, digest: str) -> None:
        """Append a fresh entry for ``path`` and flush.

        Re-stats the file to capture the current ``(mtime_ns, size)``.
        Silently no-ops if the cache was opened read-only. The cache
        file (and its parent dups folder) is created lazily on first
        write so a "no duplicates, no hashing" scan never side-effects
        the filesystem.
        """
        if not self._writable:
            return
        try:
            st = path.stat()
        except OSError as exc:
            logger.debug("hash_cache.set: stat failed for %s: %s", path, exc)
            return
        entry = {
            "path": str(path),
            "mtime_ns": st.st_mtime_ns,
            "size": st.st_size,
            "sha256": digest,
        }
        with self._lock:
            self._entries[str(path)] = (st.st_mtime_ns, st.st_size, digest)
            # Lazy-create: parent folder + header on the very first
            # write. Subsequent writes just append.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            file_existed = self._path.exists()
            with self._path.open("a", encoding="utf-8") as fh:
                if not file_existed:
                    header = {
                        "_header": {
                            "version": HASH_CACHE_VERSION,
                            "source_folder": str(self._source_folder),
                            "created_at": datetime.now(UTC).isoformat(),
                        }
                    }
                    fh.write(json.dumps(header) + "\n")
                fh.write(json.dumps(entry) + "\n")
                fh.flush()
                # fsync omitted — the cost on a per-file flush is high
                # and the worst case (lose the last few entries on a
                # power loss) is identical to the no-cache baseline.

    # --- bootstrap ------------------------------------------------------

    @classmethod
    def open(cls, *, dups_folder: Path, source_folder: Path) -> HashCache:
        """Construct a writable cache for ``dups_folder``.

        Loads existing entries if a valid cache file is present; the
        file (and the parent ``dups_folder``) are otherwise created
        lazily on the first :meth:`set` call. This keeps a "no
        duplicates, scan finds nothing to do" run side-effect-free.

        If an existing cache file is invalid (corrupt header, version
        mismatch, or source-folder mismatch), it is removed and
        treated as if missing.
        """
        path = dups_folder / HASH_CACHE_NAME
        source_resolved = source_folder.resolve()

        if path.exists():
            entries = _read_entries(path, expected_source=source_resolved)
            if entries is not None:
                return cls(
                    path=path,
                    source_folder=source_resolved,
                    entries=entries,
                    writable=True,
                )
            # Header invalid or source mismatch → start fresh.
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("hash_cache: could not remove stale cache %s: %s", path, exc)

        # Defer file creation until the first set(); see set() docstring.
        return cls(path=path, source_folder=source_resolved, entries={}, writable=True)

    @classmethod
    def load(cls, path: Path) -> HashCache | None:
        """Read-only load for inspection. Returns ``None`` if missing
        or invalid; never raises."""
        if not path.is_file():
            return None
        try:
            entries = _read_entries(path, expected_source=None)
        except OSError:
            return None
        if entries is None:
            return None
        # Pull source_folder out of the header to populate the field.
        try:
            with path.open("r", encoding="utf-8") as fh:
                first = fh.readline()
            header_line = json.loads(first)
            src = header_line.get("_header", {}).get("source_folder", "")
        except (OSError, json.JSONDecodeError):
            src = ""
        return cls(
            path=path,
            source_folder=Path(src) if src else path.parent,
            entries=entries,
            writable=False,
        )


def _read_entries(  # noqa: PLR0911 — linear validation guards
    path: Path,
    *,
    expected_source: Path | None,
) -> dict[str, tuple[int, int, str]] | None:
    """Parse a cache file. Returns the entries dict, or ``None`` if the
    header is missing/invalid/wrong-source.

    Skips any single corrupt entry line rather than discarding the
    whole cache (the worst case is one extra fresh hash, not a full
    rebuild).
    """
    entries: dict[str, tuple[int, int, str]] = {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            first = fh.readline()
            if not first:
                return None
            try:
                header_line = json.loads(first)
            except json.JSONDecodeError:
                logger.warning("hash_cache: header line is not valid JSON in %s", path)
                return None
            header = header_line.get("_header") if isinstance(header_line, dict) else None
            if not isinstance(header, dict):
                logger.warning("hash_cache: header missing in %s", path)
                return None
            if header.get("version") != HASH_CACHE_VERSION:
                logger.warning(
                    "hash_cache: version mismatch in %s (got %r, want %r); discarding",
                    path,
                    header.get("version"),
                    HASH_CACHE_VERSION,
                )
                return None
            if expected_source is not None:
                cached_source = header.get("source_folder", "")
                if cached_source != str(expected_source):
                    logger.warning(
                        "hash_cache: source_folder mismatch in %s "
                        "(cached=%r, scan=%r); discarding",
                        path,
                        cached_source,
                        str(expected_source),
                    )
                    return None

            for line_no, raw_line in enumerate(fh, start=2):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("hash_cache: skip malformed line %d in %s", line_no, path)
                    continue
                if not isinstance(obj, dict):
                    continue
                p = obj.get("path")
                m = obj.get("mtime_ns")
                s = obj.get("size")
                d = obj.get("sha256")
                if not (
                    isinstance(p, str)
                    and isinstance(m, int)
                    and isinstance(s, int)
                    and isinstance(d, str)
                ):
                    continue
                entries[p] = (m, s, d)
    except OSError as exc:
        logger.warning("hash_cache: read failed for %s: %s", path, exc)
        return None
    return entries


def seed_from_manifest(cache: HashCache, manifest_entries: list) -> int:
    """Populate ``cache`` with entries from a prior manifest's records.

    Returns the number of entries written. Useful when the cache file
    has been deleted but the manifest survives — every manifest entry
    has a ``sha256``, so we can warm the cache on resume.
    """
    n = 0
    for entry in manifest_entries:
        original = Path(entry.original_path)
        digest = getattr(entry, "sha256", None)
        if not digest:
            continue
        if not original.exists():
            # File was moved (it's a duplicate that's now in dups);
            # the kept copy at the same digest may live elsewhere.
            # We cache by ORIGINAL path: if the user replays scan after
            # restoring a previous quarantine, the original re-emerges
            # at this path. Skip if it's not currently there.
            continue
        cache.set(original, digest)
        n += 1
    return n


__all__ = [
    "HASH_CACHE_NAME",
    "HASH_CACHE_VERSION",
    "HashCache",
    "seed_from_manifest",
]
