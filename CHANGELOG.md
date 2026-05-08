# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Version bumps follow the conventional-commits convention described in `CLAUDE.md`.

## [Unreleased]

## [0.9.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.9.0) — 2026-05-08

`dedupe sweep` learns two new modes for relocating user content out of
slideshow folders. Closes #31 and #32.

### Added

- **`dedupe sweep --videos`** — move video files (`.mov`, `.mp4`,
  `.m4v`, `.avi`, `.mkv`, `.wmv`, `.flv`, `.webm`, `.mpg`, `.mpeg`,
  `.3gp`) to a sibling folder. Default destination: `<folder> - MOV`
  (matches the existing manual convention many users have for
  separating videos from a photo slideshow). Override with
  `--videos-folder PATH`. Always moves; never deletes (these are user
  content). Closes #32.
- **`dedupe sweep --non-images`** — move arbitrary non-image files
  (`.txt`, `.pdf`, `.docx`, `.zip`, `.mp3`, etc.) to a sibling folder.
  Default destination: `<folder>-non-images`. Override with
  `--non-images-folder PATH`. Always moves; never deletes. Closes #31.
- **Combinable modes.** A single `dedupe sweep <folder> --junk
  --non-images --videos` walks the source once and dispatches each
  file to the right category. Each category writes its own manifest
  in its own destination, so restores are independent.
- New constant `VIDEO_EXTENSIONS` in `sweep.py` (companion to
  `IMAGE_EXTENSIONS` and `JUNK_FILES`).

### Changed

- `run_sweep` is now multi-category: walks once, classifies each file
  (junk / video / non-image / image / other), routes to the right
  category's destination + manifest. The single-category implementation
  from v0.7.0 was a special case of this shape.
- `SweepResult` gains per-category counters (`junk_swept`,
  `non_images_swept`, `videos_swept`) alongside the aggregate
  `files_swept`. JSON output exposes them too.
- The summary block now lists per-category destinations when multiple
  categories ran in one invocation.

### Notes

- Image files are never touched by `sweep`, regardless of which modes
  are enabled — that's `scan`'s job.
- **Live Photos pairing is out of scope for v1.** A Live Photo is a
  JPG + a paired MOV with the same basename; running
  `--videos` on a folder of Live Photos splits the pairs. If
  preserving the pairing matters, file an enhancement issue.
- The "never delete files" invariant continues to hold for everything
  except `--junk` (auto-regenerated OS metadata, narrowly scoped). The
  audit's `check_no_destructive_calls.sh` continues to pass — the only
  `Path.unlink()` in `src/dedupe/` lives in `sweep.py`'s junk-deletion
  code path.

### Stats

113 tests pass (was 96; +17 new across `tests/test_sweep.py` and
`tests/test_cli.py`). Lint, pyright, audit hard gates: clean.

## [0.8.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.8.0) — 2026-05-07

DRY refactor + code-quality audit suite. **No CLI behavior change** —
every flag, default, and `--help` output is identical to v0.7.0.

### Added (audit suite, originally in #34)

- **`make audit`** — 10-check code-quality + safety suite borrowing the
  multi-check shape from a sibling project. 5 hard gates (`ruff`,
  `pyright`, `pytest --cov-fail-under=80`, `pre-commit run --all-files`,
  project-specific destructive-call safety check) and 5 report-only
  signals (`bandit`, `pip-audit`, `radon cc`, `radon mi`, `vulture`).
  Implemented as `scripts/audit.sh` with a PASS/FAIL summary table.
- **`make audit-fast`** — local-dev subset (~5s) skipping the
  pre-commit drift check and CVE scan.
- **`scripts/check_no_destructive_calls.sh`** — project-specific safety
  check that greps `src/dedupe/` for forbidden destructive patterns
  (`os.remove`, `shutil.rmtree`, `Path.rmdir`, `os.unlink`) and allows
  `Path.unlink` only inside `src/dedupe/sweep.py`. Hard-gates the audit
  to keep the "never delete files (except sweep --junk)" invariant
  automatic instead of code-review-enforced.
- **Audit GitHub Actions workflow** (`.github/workflows/audit.yml`)
  runs nightly at 09:00 UTC and on `workflow_dispatch`. Uploads the
  audit report as a 30-day workflow artifact. NOT wired into PR
  checks — fast feedback stays as the per-PR gate.
- Dev deps: `bandit`, `pip-audit`, `radon`, `xenon`, `vulture`.

### Changed (DRY refactor, originally in #35)

Three concrete duplications consolidated. **No on-disk JSON shape
changes**, **no CLI surface changes**, **no test changes** — pure
internal cleanup driven by the audit's complexity findings.

- **`src/dedupe/walk.py`** (new): shared file-tree walker plus
  `is_hidden` / `matches_exclude` / `rel` helpers. Three previous
  walkers (`scan.iter_image_files`, `sweep._iter_candidate_files`,
  inline `rglob` in `info.py`) and three filter helpers (private
  underscore-prefixed in `scan.py`, imported across modules) collapsed
  into one module with one `walk_files(opts, predicate)` generic. Each
  subcommand now uses the shared helpers; scan and sweep walkers
  become thin wrappers; info keeps its specialized walker (it counts
  hidden + broken-symlink separately) but uses the public helpers.
- **`AtomicManifestWriter[Entry]`** in `manifest.py`: generic
  atomic-flushed JSON writer. Three previous writers (`ManifestWriter`,
  `_ArchiveManifestWriter`, `_SweepManifestWriter`) collapsed to one.
  `ManifestWriter` survives as a thin compatibility shim around the
  generic so `scan.py` keeps its existing keyword-arg `add(...)` API
  and `resume_from` parameter. `_ArchiveManifestWriter` and
  `_SweepManifestWriter` deleted in favor of small factory functions.
- **`src/dedupe/cli/`** package: `cli.py` (912 lines) split into one
  file per subcommand, plus `parser.py` (build_parser, helpers,
  exit-code constants) and `output.py` (shared formatters).
  Largest file post-split: `cli/convert.py` at 266 lines. Adding a new
  subcommand becomes "create one file + add one import line in
  `cli/__init__.py`."
- `_cmd_convert` cyclomatic complexity dropped from D (23) to C (19)
  by extracting `_resolve_source_exts()`. `_cmd_sweep` complexity
  dropped from C (16) off the C+ list by extracting `_emit_summary()`.

### Audit baseline → post-refactor diff

Same 5/5 hard gates pass before and after the refactor. Report-only
findings:

- **Radon CC**: 8 functions flagged → 7. `_cmd_convert` D(23) → C(19).
  `_cmd_sweep` C(16) → off list. `iter_image_files` C(13) → off list.
  New: `walk.walk_files` C(14) — but this single function replaces
  the work of three separate ones.
- **Bandit, pip-audit, radon MI, vulture**: clean before, clean after.
- **Coverage**: 85% before, 85% after (no test changes).

### Notes

- The CLI surface is **byte-for-byte identical** to v0.7.0. `dedupe
  --help` and every subcommand's `--help` produce the same output;
  every flag works the same way.
- On-disk manifest JSON shape is preserved exactly — restore from a
  v0.7.0 manifest works against v0.8.0 unchanged.
- The new `cli/` package shadows `cli.py` (which was deleted). The
  import path `from dedupe.cli import main` continues to work because
  Python loads `__init__.py` when you `import dedupe.cli`. The
  pyproject entry point `dedupe = "dedupe.cli:main"` is unchanged.

## [0.7.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.7.0) — 2026-05-07

### Added
- **`dedupe sweep <folder> --junk`** — new subcommand for clearing
  auto-generated OS metadata files out of source folders. Hardcoded
  allowlist: `Thumbs.db`, `.DS_Store`, `desktop.ini`, `.AppleDouble`.
  By default these files are **deleted** (with a manifest log at
  `<folder>-sweep-log/sweep-manifest.json`) since they're
  auto-regenerated by their respective OSes. Pass `--quarantine-junk`
  to *move* them to `<folder>-junk/` mirroring layout instead.
  Closes #29 and #30.
- New `sweep` flags: `--junk`, `--quarantine-junk`, `--junk-folder`,
  `--log-folder`, `--dry-run`, `--recursive`/`--no-recursive`,
  `--follow-symlinks`, `--exclude`.
- `src/dedupe/sweep.py` module with `JUNK_FILES` allowlist constant,
  `SweepOptions`, `SweepResult`, `SweepEntry`, `_SweepManifestWriter`
  (atomic per-entry flush, same shape as the existing manifest writer
  for scan and convert).
- 14 new tests in `tests/test_sweep.py` plus 4 CLI integration tests.

### Notes on the safety-invariant exception
This is the **first and only place** in the tool where deletion is the
default action. The exception is narrowly scoped: a hardcoded allowlist
of 4 well-known auto-regenerated filenames, opt-in via an explicit
`--junk` flag, and every deletion is logged to a sweep manifest. For
any other category — non-image user content (#31), video files (#32) —
the default remains "move to quarantine, never delete." README has a
new `## Why sweep --junk deletes by default` section explaining the
reasoning.

## [0.6.1](https://github.com/mickmill54/image-deduper/releases/tag/v0.6.1) — 2026-05-05

Docs-only release. No code or behavior change.

### Changed
- `docs/architecture.md` gains a new **"Algorithm: how `dedupe scan`
  scales"** section after the Threading model. Covers the four-phase
  pipeline with rationale (why threads for hashing, why single-threaded
  for moves), why SHA-256 vs pairwise byte comparison, why a
  cryptographic hash specifically, memory and time profiles at 50K
  photos, the determinism + resumability properties as algorithmic
  consequences, and which tunables move the needle on big runs.
- Three new mermaid diagrams in that section:
  - **Hash-bucket grouping** — many-to-one file→hash→group
    visualization that ASCII can't render cleanly
  - **Resumable-scan decision tree** — when does a re-run resume vs
    refuse vs start fresh
  - **Manifest atomicity state diagram** — `_write()` lifecycle,
    showing why a crash mid-write never corrupts the manifest

## [0.6.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.6.0) — 2026-05-05

### Added
- **macOS standalone binary** (`dedupe-macos-arm64`) attached to every
  tagged release. Single ~40 MB file that bundles Python + Pillow +
  imagehash + pillow-heif via PyInstaller — recipients don't need
  Python installed. Apple Silicon only; Intel Macs and Linux/Windows
  binaries are out of scope for this release. Closes #20.
- New `make binary` target builds the binary locally at `dist/dedupe`.
- New `build-binary-macos` CI job runs on `macos-latest` for `v*`
  tags, builds + smoke-tests + uploads to the release.
- README install section gains a "macOS standalone binary" path with
  a Gatekeeper-quarantine workaround note.

### Changed
- `pyinstaller>=6.0` added to dev deps.
- `make clean` now also removes `dedupe.spec` (PyInstaller's spec file).

## [0.5.1](https://github.com/mickmill54/image-deduper/releases/tag/v0.5.1) — 2026-05-05

Release-engineering polish. No CLI behavior change.

### Added
- **Pyright type-check in CI.** New `pyrightconfig.json` (basic mode,
  Python 3.11+ target). `pyright` added to dev deps; new `make
  typecheck` target. CI runs pyright on every push/PR. Codebase passes
  with 0 errors. Closes #8.
- **Wheel + sdist attached to every GitHub release.** New `release`
  job in CI that fires only on `v*` tag pushes: it builds with
  `python -m build`, then uploads `dist/*.whl` and `dist/*.tar.gz` to
  the matching release page. The job creates the release with
  auto-generated notes if one doesn't exist yet, otherwise uploads to
  the existing release. `build` added to dev deps; new `make build`
  target. Closes #9.

### Changed
- `src/dedupe/ui.py`: `_RichProgress.__init__` now takes
  `rich.progress.TaskID` instead of `int` to match what
  `Progress.add_task` actually returns. Behavior unchanged; pyright
  was the only thing that noticed.
- README install section adds a "from a tagged release" path:
  `pip install git+https://github.com/.../image-deduper.git@vX.Y.Z`
  or download the wheel asset from the release page.

## [0.5.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.5.0) — 2026-05-05

### Added
- **`dedupe info <folder>`** — new read-only subcommand that walks a
  folder and reports total files, image vs non-image counts, hidden
  files, broken symlinks, total size, and a per-extension breakdown
  with sizes. Supports `--json` for machine output, `--recursive` /
  `--no-recursive`, `--exclude-hidden`, `--follow-symlinks`, and
  `--exclude PATTERN`. Closes #7.
- **`--exclude PATTERN`** flag on `scan`, `convert`, and `info`.
  Glob-style; matched against the path relative to the source folder
  AND the basename, so both `--exclude 'exports/*'` and
  `--exclude '*.tmp'` work as expected. Repeatable AND accepts
  comma-separated lists. Closes #5.
- **`--from-any`** flag on `convert`. Convenience for "convert every
  readable image format except files already matching the target."
  Mutually exclusive with `--source-ext` (returns exit 2). Closes #15.
- **Resumable scan** — if `<dups-folder>/manifest.json` already exists
  for the same source folder, `dedupe scan` resumes from it: skips
  files whose `original_path` is already recorded, appends new
  entries instead of truncating. Refuses to mix runs from a different
  source folder. Warns the user when resuming. Closes #6.
- **Comma-list flag syntax** for list-style flags. `--source-ext png,bmp,gif`
  is now equivalent to `--source-ext png --source-ext bmp --source-ext gif`,
  and the two forms can be mixed. Same applies to `--exclude`. Closes #16.

### Changed
- `manifest.py`: `ManifestWriter.__init__` accepts a `resume_from`
  Manifest to seed the writer from a pre-existing manifest (enables
  resumable scan).
- README adds `info` to the command list, documents the new flags
  per subcommand, and shows usage examples for the new flows.
- `docs/architecture.md` adds `info.py` to the module map.

## [0.4.1](https://github.com/mickmill54/image-deduper/releases/tag/v0.4.1) — 2026-05-05

Polish release bundling three quick-win backlog items. No CLI behavior change.

### Added
- **`[project.urls]`** in `pyproject.toml` (Homepage, Issues, Changelog)
  so `pip show dedupe` and any future PyPI listing surface the right links.
  Closes #2.
- **Pre-commit hooks** (`.pre-commit-config.yaml`) running `ruff check
  --fix`, `ruff format`, and pre-commit-hooks' standard hygiene hooks
  (trailing whitespace, EOF newline, YAML/TOML syntax, merge-conflict
  markers, large-file guard at 500 KB). `pre-commit` added to the dev
  dependency group; `make setup` now also runs `pre-commit install`.
  New `make hooks` target re-installs them on demand. Closes #3.
- **`make coverage`** — runs `pytest --cov=dedupe --cov-report=term-missing
  --cov-report=html`. HTML output lands at `htmlcov/index.html` (already
  in `.gitignore`). `make clean` cleans up coverage artifacts as well.
  Closes #4.

## [0.4.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.4.0) — 2026-05-05

### Added
- **`dedupe convert --in-place`** — slideshow-friendly shortcut that
  writes converted files INTO the source folder (alongside originals)
  and moves the originals to the archive folder (`<folder>-heic` by
  default). Equivalent to `--output-folder <folder> --archive-originals`,
  but as a single flag for the common HEIC-curation flow. Cannot be
  combined with `--output-folder` (returns exit code 2).
- 3 new tests: end-to-end CLI in-place flow, in-place + output-folder
  conflict, and a unit-level test that exercises the same shape via
  `ConvertOptions` directly. Closes #12.

### Changed
- README convert flag table and usage examples updated.

## [0.3.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.3.0) — 2026-05-05

### Added
- **`dedupe convert --archive-originals`** — after each successful
  conversion, *move* the original into a sibling archive folder
  (default `<folder>-heic`, override with `--archive-folder PATH`).
  Mirrors the source layout inside the archive and writes an
  `archive-manifest.json` for auditability. Off by default to preserve
  v0.2.0 behavior.
- New `convert` flags: `--archive-originals`, `--archive-folder PATH`.
- 5 new tests covering archive layout, default folder name, dry-run
  behavior, default-off (no archive without the flag), and refusal
  to overwrite an existing archive path.

### Changed
- README and `docs/architecture.md` documenting the archive flow.

## [0.2.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.2.0) — 2026-05-05

### Added
- **`dedupe convert <folder>`** — new subcommand for converting images to
  a different format. Default behavior: walks the folder for `.heic` /
  `.heif` files and writes JPEG copies into a sibling
  `<folder>-converted/` folder, mirroring the source layout. Originals
  are never modified; refuses to overwrite existing outputs.
- `convert` flags: `--to {jpeg,jpg,png,webp}` (default: `jpeg`),
  `--quality N`, `--source-ext` (repeatable, defaults to `.heic`/`.heif`),
  `--output-folder PATH`, `--dry-run`, `--recursive/--no-recursive`,
  `--threads N`, `--include-hidden`, `--follow-symlinks`.
- 11 new tests in `tests/test_convert.py` covering layout mirroring,
  overwrite refusal, dry-run, hidden-file handling, JPEG output
  validity, and a HEIC → JPEG round trip (skipped if the local
  pillow-heif build lacks the encoder).
- New Makefile targets: `make dedupe FOLDER=...`, `make heic-convert
  FOLDER=...` (hard-coded to JPEG output), and `make convert
  FOLDER=... TO=... QUALITY=...` for the general case. All accept
  extra CLI flags via `ARGS=...`.

### Changed
- README adds a `convert` flag table, end-to-end usage examples, and a
  Makefile-targets section under "Development".
- `docs/architecture.md` now documents `convert.py` in the module map
  and adds a data-flow section for `dedupe convert`.

## [0.1.1](https://github.com/mickmill54/image-deduper/releases/tag/v0.1.1) — 2026-05-05

### Added
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) running `ruff check`
  and `pytest` on every push and pull request against `main`. CI runs on
  Python 3.11, 3.12, and 3.13 to catch version-specific regressions.

### Changed
- `README.md` install section now shows the `git clone` path from the
  GitHub repo as the primary install method.
- `CHANGELOG.md` v0.1.0 heading links to the GitHub release page.

## [0.1.0](https://github.com/mickmill54/image-deduper/releases/tag/v0.1.0) — 2026-05-05

Initial release.

### Added
- `dedupe scan <folder>` — find byte-for-byte duplicate images (SHA-256) and
  move all but one of each group to a quarantine folder. Mirrors the source
  folder structure inside the quarantine. Writes a flushed-after-every-move
  JSON manifest (`manifest.json`) recording every move with the original path,
  new path, hash, kept-file path, size, and timestamp.
- `dedupe find-similar <folder>` — opt-in perceptual-hash matching for
  visually-similar images. Report-only, never moves files. Outputs a
  self-contained HTML report with base64-embedded thumbnails and a text
  summary on stdout.
- `dedupe restore <dups-folder>` — replays the manifest, moving each
  quarantined file back to its original location. Refuses to overwrite if a
  file already exists at the original path; reports conflicts and skips them.
- Global flags: `--verbose/-v`, `--quiet/-q`, `--no-color` (also respects
  `NO_COLOR`), `--json` (machine-readable output), `--version`.
- `scan` flags: `--dry-run`, `--dups-folder`, `--recursive/--no-recursive`,
  `--threads`, `--include-hidden`, `--follow-symlinks`.
- `find-similar` flags: `--threshold`, `--report`.
- HEIC support via `pillow-heif` registered at module import time.
- Threaded SHA-256 hashing using `concurrent.futures.ThreadPoolExecutor`.
- Deterministic keeper rule: shortest full path wins, alphabetical tiebreak.
- Exit codes: 0 success, 1 general error, 2 bad CLI usage (argparse default),
  3 partial failure.
- Pytest suite (30 tests) with programmatically-generated fixture images,
  covering scan, restore, find-similar, and CLI end-to-end paths.
- `Makefile` with `setup`, `test`, `lint`, `format`, `run`, `clean` targets.
- `CLAUDE.md` adapted from a sibling project, scoped to this CLI.
- `docs/architecture.md` with module map, data flow, and a class diagram.
