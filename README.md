# dedupe

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

## Duplicate definition

Two files are duplicates **if and only if** they have identical SHA-256
hashes. Same size, same resolution, same pixels, same metadata — *every byte*.
Nothing weaker counts as a duplicate. That's why `find-similar` is a separate,
report-only command: visually-similar photos are a curation decision you
should make by eye, not by hash.

## Install

From a fresh clone:

```bash
git clone git@github.com:mickmill54/image-deduper.git
cd image-deduper
make setup
source .venv/bin/activate
dedupe --help
```

Or if you already have the source:

```bash
make setup
source .venv/bin/activate
dedupe --help
```

`make setup` creates `.venv`, installs the package in editable mode, and pulls
in `pillow-heif` so `.heic` photos from iPhone are supported.

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

### `find-similar`
| Flag | Description |
|---|---|
| `--threshold <N>` | pHash Hamming distance threshold (default 5) |
| `--report <path>` | HTML output path (default `similar-report.html`) |

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
make help     # list commands
make test     # run pytest
make lint     # ruff check
make format   # ruff format + auto-fix
make clean    # remove venv and caches
```

## License

MIT
