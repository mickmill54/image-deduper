# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Version bumps follow the conventional-commits convention described in `CLAUDE.md`.

## [Unreleased]

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
