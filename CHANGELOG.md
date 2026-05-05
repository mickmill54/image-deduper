# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Version bumps follow the conventional-commits convention described in `CLAUDE.md`.

## [Unreleased]

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
