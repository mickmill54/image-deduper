# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Version bumps follow the conventional-commits convention described in `CLAUDE.md`.

## [Unreleased]

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
