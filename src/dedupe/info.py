"""`dedupe info <folder>` — print stats about a folder.

Walks a folder (same eligibility filter as scan) and tallies file count
by extension, total size, hidden-file count, broken-symlink count, etc.
Read-only — never modifies the source.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dedupe.scan import IMAGE_EXTENSIONS
from dedupe.ui import UI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InfoOptions:
    source: Path
    recursive: bool = True
    include_hidden: bool = True  # info shows everything by default
    follow_symlinks: bool = False
    exclude_patterns: tuple[str, ...] = ()


@dataclass
class InfoResult:
    source: Path
    total_files: int = 0
    image_files: int = 0
    non_image_files: int = 0
    hidden_files: int = 0
    broken_symlinks: int = 0
    total_size_bytes: int = 0
    image_size_bytes: int = 0
    by_extension: dict[str, int] = field(default_factory=dict)
    size_by_extension: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def run_info(opts: InfoOptions, ui: UI) -> InfoResult:  # noqa: PLR0912 — single-pass walker with linear filter branches
    if not opts.source.exists():
        raise FileNotFoundError(f"source folder does not exist: {opts.source}")
    if not opts.source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {opts.source}")

    ui.info(f"Inspecting [bold]{opts.source}[/bold]")

    # Apply exclude patterns via the same machinery scan uses (so the
    # behavior is consistent across commands). We DON'T use
    # iter_image_files here because info wants to count non-image files
    # too — instead we walk manually with the same hidden / symlink rules.
    from dedupe.scan import _is_hidden, _matches_exclude  # noqa: PLC0415

    walker = opts.source.rglob("*") if opts.recursive else opts.source.iterdir()

    result = InfoResult(source=opts.source)
    ext_counter: Counter[str] = Counter()
    size_counter: Counter[str] = Counter()

    for path in walker:
        try:
            if path.is_symlink():
                if not path.exists():
                    result.broken_symlinks += 1
                    continue
                if not opts.follow_symlinks:
                    continue
            if not path.is_file():
                continue
            hidden = _is_hidden(path, opts.source)
            if hidden:
                result.hidden_files += 1
                if not opts.include_hidden:
                    continue
            if _matches_exclude(path, opts.source, opts.exclude_patterns):
                continue
        except OSError as exc:
            result.errors.append(f"stat failed for {path}: {exc}")
            ui.warn(result.errors[-1])
            continue

        result.total_files += 1
        ext = path.suffix.lower() or "(no ext)"
        try:
            size = path.stat().st_size
        except OSError as exc:
            result.errors.append(f"size failed for {path}: {exc}")
            ui.warn(result.errors[-1])
            size = 0
        result.total_size_bytes += size
        ext_counter[ext] += 1
        size_counter[ext] += size
        if ext in IMAGE_EXTENSIONS:
            result.image_files += 1
            result.image_size_bytes += size
        else:
            result.non_image_files += 1

    result.by_extension = dict(ext_counter)
    result.size_by_extension = dict(size_counter)
    return result
