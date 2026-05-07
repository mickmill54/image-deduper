# dedupe

> [!IMPORTANT]
> **Platform support: macOS Apple Silicon only** (M1 / M2 / M3 / M4).
> Intel Macs, Linux, and Windows are not supported. The published macOS
> binary requires Apple Silicon; the Python wheel only installs on
> Python ≥ 3.11. If you'd like a build for another platform, please
> [open a feature request](https://github.com/mickmill54/image-deduper/issues/new?template=feature_request.yml).

Find and quarantine duplicate image files from a directory. Built for curating
photo slideshows where **safety and auditability matter more than speed**.

## What it does

- **`dedupe scan <folder>`** — finds byte-for-byte duplicate images
  (SHA-256), keeps one copy of each group, and *moves* the rest to a sibling
  quarantine folder. Never deletes. Logs every move to a JSON manifest.
- **`dedupe find-similar <folder>`** — opt-in perceptual-hash matching for
  visually-similar-but-not-identical photos (burst shots, recompressions).
  **Report only.** Outputs a self-contained HTML page with side-by-side
  thumbnails so you can pick the best one yourself.
- **`dedupe restore <dups-folder>`** — replays the manifest and moves every
  quarantined file back to its original location. Refuses to overwrite.
- **`dedupe info <folder>`** — print stats about a folder: total files,
  image vs non-image counts, total size, breakdown by extension, hidden
  files, broken symlinks. Read-only. Use `--json` for machine output.
- **`dedupe convert <folder>`** — converts images to a target format
  (default: HEIC/HEIF → JPEG). Converted copies go to a sibling
  `<folder>-converted/` folder. By default originals are *not*
  modified; pass `--archive-originals` to also *move* the originals
  into a sibling `<folder>-heic/` folder (mirrored layout, with an
  `archive-manifest.json`) so the source folder ends up free of the
  old format.

## Duplicate definition

Two files are duplicates **if and only if** they have identical SHA-256
hashes. Same size, same resolution, same pixels, same metadata — *every byte*.
Nothing weaker counts as a duplicate. That's why `find-similar` is a separate,
report-only command: visually-similar photos are a curation decision you
should make by eye, not by hash.

## Install

### Homebrew (recommended for macOS users)

```bash
brew install mickmill54/tap/dedupe
dedupe --help
```

Apple Silicon only (M1/M2/M3/M4); Intel Macs aren't supported. To upgrade
later: `brew upgrade dedupe`. The formula lives at
[`mickmill54/homebrew-tap`](https://github.com/mickmill54/homebrew-tap).

### macOS binary, manually (no Homebrew, no Python)

Same binary that the brew formula installs, but downloaded by hand:

```bash
# Always grabs the latest release — no need to update this URL
curl -L -o dedupe https://github.com/mickmill54/image-deduper/releases/latest/download/dedupe-macos-arm64
chmod +x dedupe

# First-launch Gatekeeper note: macOS may block an unsigned binary the
# first time. Either right-click → Open in Finder, or clear the
# quarantine attribute:
xattr -d com.apple.quarantine ./dedupe

./dedupe --version
./dedupe info ~/Pictures/some-folder

# (Optional) put it on your PATH so you can call it from anywhere:
mv dedupe ~/bin/dedupe   # or wherever your PATH dir lives
```

To pin a specific version instead, swap `latest` for the tag, e.g.:
`https://github.com/mickmill54/image-deduper/releases/download/v0.6.0/dedupe-macos-arm64`.

The binary is ~40 MB. Slower to start up than the venv version (~200 ms
vs ~50 ms), but doesn't require Python to be installed.

### Python wheel (pip)

If you already have a Python environment and prefer pip:

```bash
pip install git+https://github.com/mickmill54/image-deduper.git@v0.6.0
```

…or download `dedupe-X.Y.Z-py3-none-any.whl` from a release page and
`pip install` the local file. Each release ships a wheel + sdist.

### From source (developing on this repo)

```bash
git clone git@github.com:mickmill54/image-deduper.git
cd image-deduper
make setup
source .venv/bin/activate
dedupe --help
```

`make setup` creates `.venv`, installs the package in editable mode, pulls in
`pillow-heif` so `.heic` photos from iPhone are supported, and installs the
pre-commit hooks.

## Usage

```bash
# Find and quarantine exact duplicates (default: <folder>-dups as sibling)
dedupe scan ~/Pictures/naomi-slide-show

# Preview without moving anything
dedupe scan ~/Pictures/naomi-slide-show --dry-run

# Custom quarantine folder
dedupe scan ~/Pictures/naomi-slide-show --dups-folder ~/quarantine

# Find visually-similar photos (no moves, HTML report)
dedupe find-similar ~/Pictures/naomi-slide-show

# Stricter similarity threshold (lower = stricter; default 5)
dedupe find-similar ~/Pictures/naomi-slide-show --threshold 3

# Restore everything from the manifest
dedupe restore ~/Pictures/naomi-slide-show-dups

# Convert HEIC/HEIF to JPEG (output: ~/Pictures/naomi-slide-show-converted)
dedupe convert ~/Pictures/naomi-slide-show

# Convert AND archive: HEIC originals move to ~/Pictures/naomi-slide-show-heic/
# leaving the source folder free of HEIC files. archive-manifest.json records
# every move.
dedupe convert ~/Pictures/naomi-slide-show --archive-originals

# In-place: write JPGs INTO the source folder (alongside originals) and move
# originals to ~/Pictures/naomi-slide-show-heic/. One flag — best for slideshow
# software that reads the source folder directly.
dedupe convert ~/Pictures/naomi-slide-show --in-place

# Convert ANY readable format (PNG, BMP, GIF, TIFF, WebP, HEIC) to JPEG —
# existing JPGs are skipped automatically.
dedupe convert ~/Pictures/naomi-slide-show --from-any --to jpeg

# Skip subfolders during scan (glob-style, repeatable, comma-list OK)
dedupe scan ~/Pictures/naomi-slide-show --exclude 'exports/*,Trash/*'

# Inspect a folder before deciding what to do
dedupe info ~/Pictures/naomi-slide-show

# Convert PNGs to WebP at quality 85, custom output folder
dedupe convert ~/Pictures/naomi-slide-show \
  --to webp --quality 85 \
  --source-ext png \
  --output-folder ~/Pictures/webp-out
```

## Flags

### Global
| Flag | Description |
|---|---|
| `--verbose` / `-v` | More detail |
| `--quiet` / `-q` | Errors only |
| `--no-color` | Disable color (also respects `NO_COLOR` env var) |
| `--json` | Machine-readable output instead of rich console |
| `--version` | Print version and exit |

### `scan`
| Flag | Description |
|---|---|
| `--dry-run` | Report only, do not move files |
| `--dups-folder <path>` | Quarantine folder (default `<folder>-dups`) |
| `--recursive` / `--no-recursive` | Recurse into subfolders (default: yes) |
| `--threads <N>` | Hash workers (default: CPU count) |
| `--include-hidden` | Include dotfiles |
| `--follow-symlinks` | Follow symlinks |
| `--exclude <pattern>` | Glob to skip (repeatable AND comma-list); matches relative path *and* basename. e.g. `--exclude 'exports/*'` |

### `info`
| Flag | Description |
|---|---|
| `--recursive` / `--no-recursive` | Recurse into subfolders (default: yes) |
| `--exclude-hidden` | Drop dotfiles from counts (default: included) |
| `--follow-symlinks` | Follow symlinks (default: skip) |
| `--exclude <pattern>` | Glob to skip (repeatable AND comma-list) |
| `--json` | Machine-readable output |

### `find-similar`
| Flag | Description |
|---|---|
| `--threshold <N>` | pHash Hamming distance threshold (default 5) |
| `--report <path>` | HTML output path (default `similar-report.html`) |

### `convert`
| Flag | Description |
|---|---|
| `--to <format>` | Target format: `jpeg`, `jpg`, `png`, `webp` (default: `jpeg`) |
| `--quality <N>` | Encoder quality, 1–100 — JPEG/WebP only (default: 92) |
| `--source-ext <ext>` | Source extension to include — repeatable AND comma-list (default: `.heic`, `.heif`) |
| `--from-any` | Convert every readable format except files already matching the target (mutually exclusive with `--source-ext`) |
| `--output-folder <path>` | Output folder (default: `<folder>-converted`) |
| `--exclude <pattern>` | Glob to skip (repeatable AND comma-list) |
| `--archive-originals` | After each conversion, *move* the original into the archive folder (off by default) |
| `--archive-folder <path>` | Where to move originals when `--archive-originals` is set (default: `<folder>-heic`) |
| `--in-place` | Write converted files INTO the source folder and archive originals. Equivalent to `--output-folder <folder> --archive-originals`. Cannot be combined with `--output-folder`. |
| `--dry-run` | Report only, do not write files |
| `--recursive` / `--no-recursive` | Recurse into subfolders (default: yes) |
| `--threads <N>` | Worker threads (default: CPU count) |
| `--include-hidden` | Include dotfiles |
| `--follow-symlinks` | Follow symlinks |

## Manifest / restore workflow

`scan` writes `manifest.json` into the dups folder. Each entry records:

- `original_path` — where the duplicate came from
- `new_path` — where it now lives in the dups folder
- `sha256` — its hash
- `kept_path` — the surviving copy (so you can find the "winner")
- `size_bytes`
- `timestamp` (UTC ISO-8601)

The manifest is flushed after every move, so a crash mid-run still leaves a
usable record. `dedupe restore <dups-folder>` reads it and moves everything
back. If a file already exists at the original location, restore **skips and
reports it** rather than overwriting.

## Why "shortest path wins"

When picking which copy to keep, the tool uses the shortest full path, with
alphabetical tiebreak. This is deterministic and tends to favor the
canonically-named original over duplicates buried in subfolders like
`screenshots/copy/2024/IMG_1234 (1).jpg`. Re-running on the same input
produces the same outcome — useful for reasoning about manifests and restores.

## Image extensions scanned

`.jpg .jpeg .png .heic .heif .tif .tiff .bmp .gif .webp`

Hidden files (`.DS_Store`, dotfiles) are skipped by default. Pass
`--include-hidden` to override.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | General error |
| 2 | Bad CLI usage (argparse default) |
| 3 | Partial failure — some files couldn't be processed |

## Development

```bash
make help        # list commands
make test        # run pytest
make coverage    # pytest + coverage HTML report at htmlcov/index.html
make lint        # ruff check
make format      # ruff format + auto-fix
make typecheck   # static type-check with pyright
make build       # build wheel + sdist into dist/
make binary      # build single-file standalone binary at dist/dedupe
make hooks       # (re)install pre-commit hooks
make clean       # remove venv, caches, build/coverage artifacts
```

`make setup` installs pre-commit hooks into `.git/hooks/` automatically, so
`ruff check`, `ruff format`, and a few standard hygiene hooks (trailing
whitespace, end-of-file newline, YAML/TOML syntax, merge-conflict markers,
large-file detection) run on every `git commit`. Bypass once with
`git commit --no-verify` if you need to.

The Makefile also exposes the CLI as named targets, so you don't have to
remember the flag layout:

```bash
make dedupe FOLDER=~/Desktop/naomi-slide-show
make dedupe FOLDER=~/Desktop/naomi-slide-show ARGS=--dry-run
make heic-convert FOLDER=~/Desktop/naomi-slide-show
make convert FOLDER=~/Pictures/foo TO=webp QUALITY=85
```

Pass extra `dedupe` flags through `ARGS=...`. `make heic-convert` is
hard-coded to `--to jpeg` (the slideshow-friendly default); `make
convert` honors `TO=...` for any of `jpeg`, `png`, or `webp`.

## License

MIT — see [`LICENSE`](LICENSE) for the full text.
