"""Generic file-tree walker shared by all subcommands.

Three subcommands previously had their own walkers:
  - `scan.iter_image_files` — filtered by IMAGE_EXTENSIONS, hidden, exclude
  - `sweep._iter_candidate_files` — no extension filter (junk files are hidden)
  - `info.run_info` — inline rglob with all-files counting

Plus three filter helpers (`_is_hidden`, `_matches_exclude`, `_rel`) that
lived in `scan.py` but were imported by `convert.py`, `sweep.py`, and
`info.py` — the wrong shape for a "shared by everyone" utility.

This module pulls all of that into one place. Each subcommand passes
its own `predicate` to `walk_files` to encode subcommand-specific
filtering on top of the shared shape (symlinks, hidden, exclude).

Helper functions are intentionally `is_hidden` / `matches_exclude` /
`rel` (no underscore) — they're public utilities now, not private to
scan.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalkOptions:
    """Shared walk configuration. Each subcommand subclasses or composes
    this into its own options dataclass; `as_walk_options()` extractors
    keep the contract narrow.
    """

    source: Path
    recursive: bool = True
    follow_symlinks: bool = False
    include_hidden: bool = False
    exclude_patterns: tuple[str, ...] = ()


def is_hidden(path: Path, source: Path) -> bool:
    """A path is hidden if any component (relative to source) starts with '.'."""
    try:
        rel_path = path.relative_to(source)
    except ValueError:
        rel_path = path
    return any(part.startswith(".") for part in rel_path.parts)


def matches_exclude(path: Path, source: Path, patterns: tuple[str, ...]) -> bool:
    """True if `path` matches any glob in `patterns`.

    Each pattern is matched twice: once against the relative path
    (e.g. "exports/img.jpg") and once against the basename (e.g. "img.jpg").
    Either match excludes the file. Uses fnmatch semantics, which matches
    most users' intuition for shell-style globs (`*.tmp`, `exports/*`,
    `**/.DS_Store`).
    """
    if not patterns:
        return False
    try:
        rel_path = str(path.relative_to(source))
    except ValueError:
        rel_path = str(path)
    name = path.name
    return any(fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(name, pat) for pat in patterns)


def rel(path: Path, source: Path) -> str:
    """Render `path` relative to `source` for log output. Falls back to absolute."""
    try:
        return str(path.resolve().relative_to(source.resolve()))
    except ValueError:
        return str(path)


def walk_files(
    opts: WalkOptions, predicate: Callable[[Path], bool] | None = None
) -> Iterator[Path]:
    """Yield regular files under `opts.source` matching the shared filters
    plus the caller's `predicate`.

    Shared filters (in order):
      1. symlink: skipped unless `opts.follow_symlinks`
      2. is_file: directories and special files skipped
      3. include_hidden: hidden paths skipped unless flag set
      4. exclude_patterns: any glob match excludes the path

    `predicate` is the subcommand-specific filter. Pass `None` to accept
    every file that survives the shared filters (used by `sweep` and
    `info`, which apply their own narrowing afterwards).

    Errors during `is_symlink`/`is_file`/`stat` are logged and the path
    is skipped — the walk never aborts on a single inaccessible file.
    """
    if not opts.source.exists() or not opts.source.is_dir():
        return

    walker: Iterable[Path] = opts.source.rglob("*") if opts.recursive else opts.source.iterdir()

    for path in walker:
        try:
            if path.is_symlink() and not opts.follow_symlinks:
                continue
            if not path.is_file():
                continue
            if not opts.include_hidden and is_hidden(path, opts.source):
                continue
            if matches_exclude(path, opts.source, opts.exclude_patterns):
                continue
            if predicate is not None and not predicate(path):
                continue
        except OSError as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue
        yield path
