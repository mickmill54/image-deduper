"""Manifest: append-only JSON record of every duplicate move.

The manifest is the source of truth for `restore`. It is flushed after every
entry, so a crash mid-scan still leaves a usable manifest.

On-disk format (pretty-printed for human auditability):

    {
      "version": 1,
      "created_at": "2026-05-04T12:00:00+00:00",
      "source_folder": "/abs/path/to/source",
      "dups_folder": "/abs/path/to/source-dups",
      "entries": [
        {
          "original_path": "...",
          "new_path": "...",
          "sha256": "...",
          "kept_path": "...",
          "size_bytes": 12345,
          "timestamp": "2026-05-04T12:00:01+00:00"
        }
      ]
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class ManifestEntry:
    original_path: str
    new_path: str
    sha256: str
    kept_path: str
    size_bytes: int
    timestamp: str


@dataclass
class Manifest:
    source_folder: str
    dups_folder: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: int = MANIFEST_VERSION
    entries: list[ManifestEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "source_folder": self.source_folder,
            "dups_folder": self.dups_folder,
            "entries": [asdict(e) for e in self.entries],
        }


class ManifestWriter:
    """Incremental writer. Flushes the full manifest after every entry."""

    def __init__(self, path: Path, source_folder: Path, dups_folder: Path) -> None:
        self.path = path
        self.manifest = Manifest(
            source_folder=str(source_folder.resolve()),
            dups_folder=str(dups_folder.resolve()),
        )
        self._write()

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
        self.manifest.entries.append(entry)
        self._write()
        return entry

    def _write(self) -> None:
        # Atomic-ish: write to a tempfile and rename, so a crash mid-write
        # never leaves the manifest truncated.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.manifest.to_dict(), fh, indent=2, sort_keys=False)
            fh.write("\n")
            fh.flush()
        tmp.replace(self.path)


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
