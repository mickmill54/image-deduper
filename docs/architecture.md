# dedupe — architecture

This document describes how `dedupe` is laid out, how data flows through it,
and which design choices are load-bearing. Read it before making non-trivial
changes.

## Goals

- **Safety over speed.** Never delete; only move. Refuse to overwrite. Write
  the manifest before / after every move so a crash mid-run leaves an audit
  trail.
- **Determinism.** "Shortest path wins, alphabetical tiebreak" — re-running
  on the same input produces the same outcome.
- **Auditability.** The dups-folder layout mirrors the source, and the
  manifest records every move with hash, paths, size, and timestamp.
- **Lean dependencies.** `argparse` (stdlib) + `rich` for output. Pillow,
  pillow-heif, and imagehash are imported only inside `similar.py`.

## Module map

```
src/dedupe/
├── __init__.py         __version__
├── __main__.py         python -m dedupe
├── cli.py              argparse parser, subcommand dispatch
├── ui.py               rich-backed console; respects --quiet, --json, --no-color, NO_COLOR
├── manifest.py         JSON manifest read/write (atomic incremental writes)
├── scan.py             SHA-256 hashing, grouping, move-to-quarantine
├── restore.py          manifest replay with conflict detection
├── similar.py          perceptual hash, grouping, HTML report
├── convert.py          image format conversion (originals untouched)
└── info.py             read-only folder stats / breakdown (no mutation)
```

Rules of thumb:

- Only `ui.py` writes to stdout/stderr. Every other module takes a `UI`
  instance and routes through it.
- `similar.py` and `convert.py` are the modules that import Pillow /
  imagehash / pillow-heif. `cli.py` defers their imports lazily so
  `dedupe scan` doesn't load the imaging stack.
- `scan.py`, `restore.py`, `similar.py`, and `convert.py` each expose a
  single `run_*` entry point. CLI handlers in `cli.py` build the options
  dataclass and call it.
- Filesystem mutation is confined to `_move_one()` in `scan.py`, the
  `shutil.move(...)` call in `restore.py`, and the `Image.save(...)` call
  in `convert.py` (writing to a *new* output file only). There are no
  `unlink`, `rmtree`,
  or `os.remove` calls anywhere. This is enforced by code review, not by a
  linter — if you find yourself reaching for one, stop and check
  `CLAUDE.md` "Safety Invariants."

## Data flow — `dedupe scan <folder>`

```
folder
  │
  ▼
iter_image_files()         walk + filter (extension, hidden, symlinks)
  │  list[Path]
  ▼
_hash_all()                ThreadPoolExecutor → SHA-256 stream-hash each file
  │  dict[hash, list[Path]]
  ▼
filter groups where len > 1
  │  duplicate groups
  ▼
for each group:
  pick_keeper()            shortest-path, alphabetical tiebreak
  for each loser:
    _mirror_destination()  source/foo/x.jpg → dups/foo/x.jpg
    _move_one()            shutil.move; refuses to overwrite
    ManifestWriter.add()   atomic write of full manifest after every entry
  │
  ▼
ScanResult                 counts, errors, list[ManifestEntry]
```

Failure modes and what they map to:

| What goes wrong | Reported as |
|---|---|
| File can't be opened for hashing | `errors[]` entry, file skipped, exit code 3 |
| `stat()` fails on a duplicate | `errors[]`, file skipped, exit code 3 |
| Destination already exists in dups folder | `FileExistsError` → `errors[]`, exit code 3 |
| Source folder doesn't exist | `FileNotFoundError` from `run_scan` → exit code 1 |
| Bad CLI usage | argparse default → exit code 2 |
| Clean run, no errors | exit code 0 |

## Data flow — `dedupe restore <dups-folder>`

```
dups-folder
  │
  ▼
manifest.load()            reads manifest.json, validates version
  │
  ▼
for each entry:
  if not entry.new_path.exists():    → error (missing in dups)
  elif entry.original_path.exists(): → conflict, skipped
  else:                              → shutil.move(new → original)
  │
  ▼
RestoreResult              files_restored, files_skipped, conflicts, errors
```

Conflict policy: **always skip, never overwrite.** This is the
reverse-direction analog of the "refuse to overwrite" rule in `scan`.

## Data flow — `dedupe find-similar <folder>`

```
folder
  │
  ▼
iter_image_files()         (same eligibility rules as scan)
  │
  ▼
_compute_phash() per file  Pillow open → imagehash.phash, hash_size=8 (64-bit)
  │  list[(Path, ImageHash)]
  ▼
_group_by_threshold()      union-find; (i, j) merged iff hamming_distance <= threshold
  │  list[list[Path]]      singletons dropped
  ▼
console summary            "Group N · M images, anchor pHash …"
  │
  ▼
_write_html_report()       self-contained HTML; thumbnails as base64 data URIs
```

`find-similar` is **structurally read-only**: there are no `shutil.move` or
file-mutation calls anywhere in `similar.py`. A test
(`test_find_similar_does_not_move_files`) asserts the source folder is
unchanged after the run.

## Data flow — `dedupe convert <folder>`

```
folder
  │
  ▼
iter_image_files()         (same eligibility rules as scan)
  │
  ▼
filter by source_exts      default: {.heic, .heif}; overridable via --source-ext
  │  list[Path]
  ▼
plan output paths          mirror layout into <folder>-converted/, swap extension
  │  list[(src, dest)]
  ▼
ThreadPoolExecutor         _convert_one() per pair: open → convert → save
  │  raises FileExistsError if dest already exists (refuse to overwrite)
  ▼
[optional] archive pass    if --archive-originals:
                             for each successful conversion, sequentially move
                             the original into <folder>-heic/ (mirroring
                             layout) and append an entry to
                             archive-manifest.json
  │
  ▼
ConvertResult              files_scanned, files_converted, files_skipped,
                           bytes_written, files_archived, errors,
                           conversions, archive_entries
```

Without `--archive-originals` (the default), `convert` never modifies
the source folder — the only filesystem mutation is `Image.save(dest,
...)` writing to a fresh output path.

With `--archive-originals`, originals are **moved** (not deleted) into
the archive folder via `shutil.move`, and the move is recorded in an
`archive-manifest.json` flushed-after-every-entry. The archive pass is
single-threaded and runs *after* the parallel conversion phase, so the
manifest order matches the conversion order and we don't need a lock
around manifest writes outside `_ArchiveManifestWriter`'s own lock.

These guarantees are covered by:
- `test_convert_jpg_to_png_mirrors_layout` — originals untouched without the flag
- `test_archive_off_by_default_originals_remain` — explicit no-side-effect default
- `test_archive_originals_moves_sources_and_writes_manifest` — full archive flow
- `test_archive_default_folder_is_folder_dash_heic` — default name
- `test_archive_dry_run_moves_nothing` — dry-run respected
- `test_archive_refuses_to_overwrite` — pre-existing archive path is preserved
- `test_refuses_to_overwrite` — pre-existing output path is preserved

## Class diagram

The codebase is mostly functions plus small frozen dataclasses (option
records, result records, manifest entries). The only class with
non-trivial behavior is `UI` (and its progress-handle helpers); the
`ManifestWriter` is a thin wrapper around append-and-flush.

```
┌──────────────────────────┐         ┌──────────────────────────┐
│ UIConfig (dataclass)     │         │ Manifest (dataclass)     │
│ - verbose: bool          │         │ - version: int           │
│ - quiet: bool            │         │ - created_at: str        │
│ - no_color: bool         │         │ - source_folder: str     │
│ - json_mode: bool        │         │ - dups_folder: str       │
└────────────┬─────────────┘         │ - entries: list[…]       │
             │                       └────────────┬─────────────┘
             ▼                                    ▲
┌──────────────────────────┐                      │ aggregates
│ UI                       │         ┌────────────┴─────────────┐
│ + info / detail /        │         │ ManifestEntry (frozen)   │
│   success / warn / error │         │ - original_path: str     │
│ + emit_json              │         │ - new_path: str          │
│ + progress (cm)          │         │ - sha256: str            │
└──────────────────────────┘         │ - kept_path: str         │
             ▲                       │ - size_bytes: int        │
             │ used by               │ - timestamp: str         │
             │                       └──────────────────────────┘
┌────────────┼──────────────────────┐                ▲
│            │                      │                │ writes
│  ┌─────────┴──────────┐           │   ┌────────────┴───────────┐
│  │ run_scan(opts, ui) │──────────────▶│ ManifestWriter         │
│  └────────────────────┘           │   │ + add(...)             │
│  ┌────────────────────┐           │   │ - _write() (atomic)    │
│  │ run_restore(...)   │──┐        │   └────────────────────────┘
│  └────────────────────┘  │        │
│  ┌────────────────────┐  │ reads  │
│  │ run_find_similar(…)│  │        │
│  └────────────────────┘  │        │   ┌────────────────────────┐
│                          └────────────│ manifest.load(path)    │
│   command handlers in cli.py      │   └────────────────────────┘
└───────────────────────────────────┘

  Options & results (all @dataclass(frozen=True) unless noted):

    ScanOptions          ScanResult              SimilarOptions
    RestoreOptions       RestoreResult           SimilarResult
    SimilarGroup
```

The arrow conventions: solid arrows are "uses / calls"; the `aggregates`
arrow on the right shows that a `Manifest` holds a list of `ManifestEntry`.

## Why argparse instead of Click / Typer?

The spec mandates argparse — keeps the dependency footprint minimal and
gives us argparse's exit-code-2 behavior on bad input for free. The
trade-off is verbosity in subparser setup, which is contained in
`_build_parser()` and `_add_global_flags()` in `cli.py`.

## Threading model

- `scan` uses `ThreadPoolExecutor(max_workers=opts.threads or None)`.
  Hashing is I/O-bound (read big files from disk); GIL is released during
  the read syscall, so threads are the right shape here.
- `find-similar` is currently single-threaded. Pillow + imagehash do real
  work in Python and don't release the GIL, so threading would not help.
  If we ever care, switch to `ProcessPoolExecutor` — but that would force
  `pillow-heif` registration in each worker, which is annoying to get
  right. Not worth the complexity until profiling says so.

## Manifest format and forward compatibility

`manifest.json` carries an explicit `"version": 1` field. `manifest.load`
refuses any other version with a clear error. If the schema ever needs to
change, bump the version, write a migration in `manifest.py`, and update
the `MANIFEST_VERSION` constant.

The on-disk format is pretty-printed JSON — bigger on disk, but trivially
inspectable from a shell when something goes wrong, which matters more
than file size for a manifest that grows linearly with duplicates moved.

## Testing strategy

- `tests/conftest.py` builds fixture trees programmatically with Pillow.
  No checked-in binary fixtures — the `git diff` for any test change is
  always a Python-source diff.
- `fixture_tree` exercises the scan/restore round trip with two duplicate
  groups (one JPEG-cluster, one PNG-cluster) plus hidden files and nested
  subdirectories.
- `similar_tree` engineers byte-different / pHash-identical images so the
  similarity test is deterministic.
- The CLI tests (`test_cli.py`) call `dedupe.cli.main(argv)` directly so
  they're fast, in-process, and capture exit codes cleanly.

## Adding a new subcommand

1. Add a module under `src/dedupe/` exposing a `run_*` function and an
   `*Options` dataclass.
2. Add a subparser block + `_cmd_*` handler in `cli.py`. Register it with
   `set_defaults(func=...)`.
3. Add tests under `tests/`. Reuse `fixture_tree`/`similar_tree` if the
   shape fits, or add a new fixture in `conftest.py`.
4. Document the flags in `README.md` and add an entry in `CHANGELOG.md`.
