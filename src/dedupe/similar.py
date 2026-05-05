"""Perceptual-hash similarity finder. Report only — never moves files.

Algorithm:
  1. Walk the source folder for image files (same eligibility rules as scan).
  2. Compute a perceptual hash (pHash) for each image.
  3. Build groups by Hamming-distance threshold using a simple
     union-find pass. Two images go in the same group if their pHashes
     differ by <= threshold bits.
  4. Emit a self-contained HTML report (base64-embedded thumbnails) and a
     short text summary on stdout.

This module is the only place that imports Pillow and imagehash. Keeping
those imports here makes the rest of the package fast to import and
testable without optional dependencies.
"""

from __future__ import annotations

import base64
import html
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

import imagehash
from PIL import Image

from dedupe.scan import ScanOptions, iter_image_files
from dedupe.ui import UI

logger = logging.getLogger(__name__)

THUMB_SIZE = (240, 240)
PHASH_SIZE = 8  # 64-bit hash; matches imagehash default

# Register HEIC support if available; if not, .heic files will simply be
# skipped with a warning when Pillow tries to open them.
try:  # pragma: no cover - import-time side effect
    import pillow_heif  # type: ignore[import-not-found]

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    logger.debug("pillow-heif not available; HEIC files will be skipped")


@dataclass(frozen=True)
class SimilarOptions:
    source: Path
    threshold: int = 5
    report_path: Path = Path("similar-report.html")


@dataclass(frozen=True)
class SimilarGroup:
    phash_anchor: str
    members: list[Path]


@dataclass
class SimilarResult:
    files_scanned: int = 0
    groups: list[SimilarGroup] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _scan_options_for(source: Path) -> ScanOptions:
    """Reuse scan.iter_image_files with a minimal options shim."""
    return ScanOptions(source=source, dups_folder=source, recursive=True)


def _compute_phash(path: Path) -> imagehash.ImageHash:
    """Return a perceptual hash of the image at `path`, or raise."""
    with Image.open(path) as img:
        img.load()
        return imagehash.phash(img, hash_size=PHASH_SIZE)


def _make_thumbnail_data_uri(path: Path) -> str | None:
    """Return a base64 data URI for a small JPEG thumbnail of `path`, or None on error."""
    try:
        with Image.open(path) as img:
            img.load()
            rgb = img.convert("RGB")
            rgb.thumbnail(THUMB_SIZE)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:
        logger.warning("thumbnail failed for %s: %s", path, exc)
        return None


def _group_by_threshold(items: list[tuple[Path, object]], threshold: int) -> list[list[Path]]:
    """Union-find grouping by Hamming distance <= threshold.

    items: list of (path, phash). Paths in the same connected component
    (under the threshold relation) end up in the same group. Singletons
    are excluded from the returned list.
    """
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if (items[i][1] - items[j][1]) <= threshold:  # type: ignore[operator]
                union(i, j)

    components: dict[int, list[Path]] = {}
    for i, (path, _) in enumerate(items):
        components.setdefault(find(i), []).append(path)

    groups = [sorted(c, key=str) for c in components.values() if len(c) > 1]
    groups.sort(key=lambda g: str(g[0]))
    return groups


def run_find_similar(opts: SimilarOptions, ui: UI) -> SimilarResult:
    if not opts.source.exists():
        raise FileNotFoundError(f"source folder does not exist: {opts.source}")
    if not opts.source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {opts.source}")

    ui.info(f"Scanning [bold]{opts.source}[/bold] for similar images")

    files = sorted(iter_image_files(_scan_options_for(opts.source)))
    ui.detail(f"found {len(files)} candidate image file(s)")

    result = SimilarResult(files_scanned=len(files))
    if not files:
        ui.info("no image files found")
        return result

    items: list[tuple[Path, object]] = []
    with ui.progress("Hashing (perceptual)", total=len(files)) as progress:
        for path in files:
            try:
                phash = _compute_phash(path)
            except Exception as exc:  # noqa: BLE001 — Pillow can raise many things
                msg = f"phash failed for {path}: {exc}"
                result.errors.append(msg)
                ui.warn(msg)
                progress.advance(current=path.name)
                continue
            items.append((path, phash))
            progress.advance(current=path.name)

    raw_groups = _group_by_threshold(items, opts.threshold)
    phash_lookup = {p: ph for p, ph in items}
    for paths in raw_groups:
        anchor = str(phash_lookup[paths[0]])
        result.groups.append(SimilarGroup(phash_anchor=anchor, members=list(paths)))

    if not result.groups:
        ui.success("no similar groups found")
        return result

    # Console summary (the user asked for "text summary to stdout").
    ui.info("")
    ui.info(f"[bold]{len(result.groups)} similar group(s)[/bold]")
    for i, g in enumerate(result.groups, start=1):
        ui.info(f"  group {i} ({len(g.members)} images, anchor pHash {g.phash_anchor[:16]}):")
        for m in g.members:
            ui.info(f"    {m}")

    _write_html_report(result, opts, ui)
    return result


def _write_html_report(result: SimilarResult, opts: SimilarOptions, ui: UI) -> None:
    out = opts.report_path
    out.parent.mkdir(parents=True, exist_ok=True)

    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append("<html lang='en'><head><meta charset='utf-8'>")
    parts.append("<title>dedupe — similar images report</title>")
    parts.append(
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;"
        "margin:2rem;color:#222;background:#fafafa}"
        "h1{margin-bottom:.25rem}"
        ".meta{color:#666;margin-bottom:2rem}"
        ".group{background:#fff;border:1px solid #ddd;border-radius:8px;"
        "padding:1rem;margin-bottom:1.5rem}"
        ".group h2{margin:0 0 .5rem;font-size:1rem;color:#444}"
        ".thumbs{display:flex;flex-wrap:wrap;gap:1rem}"
        ".thumb{flex:0 0 auto;text-align:center;max-width:260px}"
        ".thumb img{display:block;max-width:240px;max-height:240px;"
        "border:1px solid #ccc;border-radius:4px;background:#eee}"
        ".thumb .path{font-size:.75rem;color:#555;word-break:break-all;"
        "margin-top:.25rem}"
        "</style>"
    )
    parts.append("</head><body>")
    parts.append("<h1>Similar images report</h1>")
    parts.append(
        f"<div class='meta'>Source: <code>{html.escape(str(opts.source))}</code> &middot; "
        f"threshold: {opts.threshold} &middot; "
        f"{len(result.groups)} group(s) &middot; "
        f"{result.files_scanned} files scanned</div>"
    )

    for i, group in enumerate(result.groups, start=1):
        parts.append("<section class='group'>")
        parts.append(
            f"<h2>Group {i} &middot; {len(group.members)} images &middot; "
            f"anchor pHash <code>{html.escape(group.phash_anchor)}</code></h2>"
        )
        parts.append("<div class='thumbs'>")
        for path in group.members:
            data_uri = _make_thumbnail_data_uri(path)
            parts.append("<div class='thumb'>")
            if data_uri:
                parts.append(f"<img src='{data_uri}' alt=''>")
            else:
                parts.append(
                    "<div class='thumb' style='width:240px;height:240px'>" "(no preview)</div>"
                )
            parts.append(f"<div class='path'>{html.escape(str(path))}</div>")
            parts.append("</div>")
        parts.append("</div></section>")

    parts.append("</body></html>")
    out.write_text("\n".join(parts), encoding="utf-8")
    ui.success(f"wrote report: {out}")
