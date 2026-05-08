"""Manifests: append-only JSON records of mutations the tool performed.

Three concrete manifests share the same on-disk shape and write
algorithm:

  - `manifest.json`         (scan: which dup was moved where)
  - `archive-manifest.json` (convert --archive-originals: where the
                             original went and what replaced it)
  - `sweep-manifest.json`   (sweep --junk: what was deleted or moved)

The atomic-flush-after-every-entry algorithm is identical across all
three. This module provides one generic `AtomicManifestWriter[Entry]`
that any subcommand can configure with its own header dict and entry
type.

`ManifestWriter` (no generic) is a thin compatibility wrapper that
preserves the keyword-argument `add(...)` API scan.py used historically
and adds resume_from support specific to scan's resumable-runs feature.

On-disk format (pretty-printed for human auditability):

    {
      "version": 1,
      "created_at": "2026-05-04T12:00:00+00:00",
      ...header fields specific to the subcommand...,
      "entries": [
        ...subcommand-specific entry shape...
      ]
    }
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1

# The generic atomic writer is parameterized by entry type. Each
# subcommand passes its own `entry_to_dict` (typically `dataclasses.asdict`).
EntryT = TypeVar("EntryT")


@dataclass(frozen=True)
class ManifestEntry:
    """Scan/restore manifest entry: one record of a moved duplicate."""

    original_path: str
    new_path: str
    sha256: str
    kept_path: str
    size_bytes: int
    timestamp: str


@dataclass
class Manifest:
    """In-memory representation of a scan/restore manifest. Used only by
    `manifest.load()` for restore + resumable scan; the writer side now
    goes through `AtomicManifestWriter`."""

    source_folder: str
    dups_folder: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: int = MANIFEST_VERSION
    entries: list[ManifestEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "source_folder": self.source_folder,
            "dups_folder": self.dups_folder,
            "entries": [asdict(e) for e in self.entries],
        }


class AtomicManifestWriter(Generic[EntryT]):
    """Append-only JSON writer with atomic per-entry flush.

    Each subcommand constructs one with:
      - `path`: where the manifest lives on disk
      - `header`: a dict of top-level fields (version, created_at,
        source_folder, etc.) — anything that isn't `entries`
      - `entry_to_dict`: serializer for one entry; for dataclass
        entries, pass `dataclasses.asdict`

    `add(entry)` appends + flushes under a thread lock. The flush
    writes to `<path>.tmp` and atomically renames it on top of
    `<path>`, so a crash mid-write never corrupts the manifest.

    `add_existing_entries(entries_as_dicts)` lets a subcommand seed the
    writer with pre-existing entries (used by scan's resumable-runs
    feature: load the existing manifest, replay its entries into the
    fresh writer, then continue appending new ones).
    """

    def __init__(
        self,
        path: Path,
        *,
        header: dict[str, Any],
        entry_to_dict: Callable[[EntryT], dict[str, Any]] = asdict,  # type: ignore[assignment]
    ) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._header = dict(header)  # defensive copy
        self._entries: list[dict[str, Any]] = []
        self._entry_to_dict = entry_to_dict
        self._flush()

    def add(self, entry: EntryT) -> None:
        with self._lock:
            self._entries.append(self._entry_to_dict(entry))
            self._flush()

    def add_existing_entries(self, entries: Iterable[dict[str, Any]]) -> None:
        """Seed with already-serialized entries. Used for resumable runs."""
        with self._lock:
            self._entries.extend(entries)
            self._flush()

    def _flush(self) -> None:
        payload = {**self._header, "entries": self._entries}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False)
            fh.write("\n")
            fh.flush()
        tmp.replace(self.path)


class ManifestWriter:
    """Compatibility wrapper for scan's manifest.

    Preserves the keyword-argument `add(...)` API that scan.py used
    historically and adds resume-from support specific to scan's
    resumable-runs feature. Underneath it's a thin layer over
    `AtomicManifestWriter[ManifestEntry]`.
    """

    def __init__(
        self,
        path: Path,
        source_folder: Path,
        dups_folder: Path,
        *,
        resume_from: Manifest | None = None,
    ) -> None:
        if resume_from is not None:
            header = {
                "version": resume_from.version,
                "created_at": resume_from.created_at,
                "source_folder": resume_from.source_folder,
                "dups_folder": resume_from.dups_folder,
            }
            seed_entries: list[dict[str, Any]] = [asdict(e) for e in resume_from.entries]
        else:
            header = {
                "version": MANIFEST_VERSION,
                "created_at": datetime.now(UTC).isoformat(),
                "source_folder": str(source_folder.resolve()),
                "dups_folder": str(dups_folder.resolve()),
            }
            seed_entries = []
        self._writer: AtomicManifestWriter[ManifestEntry] = AtomicManifestWriter(
            path, header=header
        )
        if seed_entries:
            self._writer.add_existing_entries(seed_entries)

    @property
    def path(self) -> Path:
        return self._writer.path

    def add(
        self,
        *,
        original_path: Path,
        new_path: Path,
        sha256: str,
        kept_path: Path,
        size_bytes: int,
    ) -> ManifestEntry:
        entry = ManifestEntry(
            original_path=str(original_path),
            new_path=str(new_path),
            sha256=sha256,
            kept_path=str(kept_path),
            size_bytes=size_bytes,
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._writer.add(entry)
        return entry


def load(path: Path) -> Manifest:
    """Load a manifest from disk. Raises FileNotFoundError or ValueError."""
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, dict):
        raise ValueError(f"manifest is not a JSON object: {path}")

    version = data.get("version")
    if version != MANIFEST_VERSION:
        raise ValueError(f"unsupported manifest version: {version!r} (expected {MANIFEST_VERSION})")

    raw_entries = data.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ValueError("manifest 'entries' is not a list")

    entries = [ManifestEntry(**e) for e in raw_entries]

    return Manifest(
        version=version,
        created_at=data.get("created_at", ""),
        source_folder=data.get("source_folder", ""),
        dups_folder=data.get("dups_folder", ""),
        entries=entries,
    )
