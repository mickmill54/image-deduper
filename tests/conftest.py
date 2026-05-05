"""Shared fixtures for the dedupe test suite.

Generates synthetic images programmatically so tests do not depend on
checked-in binary fixtures. Each fixture image is small and deterministic.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def _make_image(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 4, size[0] - 4, size[1] - 4], outline=(255, 255, 255), width=2)
    img.save(path, format="JPEG", quality=90)
    return path


def _make_png(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    img.save(path, format="PNG")
    return path


@pytest.fixture
def make_image() -> Callable[..., Path]:
    """Create a unique JPEG at `path` with `color`."""
    return _make_image


@pytest.fixture
def make_png() -> Callable[..., Path]:
    return _make_png


@pytest.fixture
def fixture_tree(tmp_path: Path) -> Path:
    """A folder of images with known duplicate structure.

    Layout:
        root/
          unique_a.jpg          (red)
          unique_b.jpg          (green)
          dup1.jpg              (blue) — keeper of group A (shortest path)
          subdir/dup1_copy.jpg  (blue) — duplicate of dup1.jpg
          deep/nested/dup1_copy2.jpg  (blue) — another duplicate of dup1.jpg
          dup2.png              (yellow)
          archive/dup2_copy.png (yellow)
          .hidden_dup.jpg       (blue) — same bytes as dup1.jpg, hidden
    """
    root = tmp_path / "root"
    blue = (10, 20, 200)
    yellow = (220, 200, 30)

    _make_image(root / "unique_a.jpg", (200, 30, 30))
    _make_image(root / "unique_b.jpg", (30, 200, 30))

    blue_master = _make_image(root / "dup1.jpg", blue)
    (root / "subdir").mkdir()
    shutil.copy2(blue_master, root / "subdir" / "dup1_copy.jpg")
    (root / "deep" / "nested").mkdir(parents=True)
    shutil.copy2(blue_master, root / "deep" / "nested" / "dup1_copy2.jpg")
    shutil.copy2(blue_master, root / ".hidden_dup.jpg")

    yellow_master = _make_png(root / "dup2.png", yellow)
    (root / "archive").mkdir()
    shutil.copy2(yellow_master, root / "archive" / "dup2_copy.png")

    return root


@pytest.fixture
def convert_tree(tmp_path: Path) -> Path:
    """Folder with a mix of files for convert tests.

    Layout:
        root/
          a.jpg                   (red)
          b.png                   (green)
          sub/c.jpg               (blue)
          sub/d.bmp               (yellow)
          .hidden.jpg             (skipped unless --include-hidden)
    """
    root = tmp_path / "convert_root"
    _make_image(root / "a.jpg", (200, 30, 30))
    _make_png(root / "b.png", (30, 200, 30))
    _make_image(root / "sub/c.jpg", (30, 30, 200))
    (root / "sub").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (220, 200, 30)).save(root / "sub/d.bmp", format="BMP")
    _make_image(root / ".hidden.jpg", (50, 50, 50))
    return root


@pytest.fixture
def heic_tree(tmp_path: Path) -> Path | None:
    """Folder with one HEIC file, or None if writing HEIC isn't supported.

    Tests using this fixture skip when it returns None — pillow-heif builds
    sometimes lack the HEIF encoder.
    """
    try:
        import pillow_heif  # noqa: PLC0415 — optional dep, deferred to fixture call

        pillow_heif.register_heif_opener()
    except Exception:
        return None

    root = tmp_path / "heic_root"
    root.mkdir()
    img = Image.new("RGB", (64, 64), (180, 60, 60))
    target = root / "photo.heic"
    try:
        img.save(target, format="HEIF")
    except Exception:
        return None
    if not target.is_file() or target.stat().st_size == 0:
        return None
    return root


@pytest.fixture
def similar_tree(tmp_path: Path) -> Path:
    """A folder with visually-similar but byte-different images for find-similar tests.

    The "burst" trio shares the same richly-textured layout, so their pHashes
    are stable (Hamming distance 0 on these fixtures) while their JPEG bytes
    differ — exact same input the CLI is designed to handle.
    """
    root = tmp_path / "similar_root"
    root.mkdir()

    # Richly-textured base image: distinct shapes give phash plenty of features
    # to anchor on, so small JPEG/crop variations don't move the hash.
    src = Image.new("RGB", (256, 256), (180, 200, 220))
    d = ImageDraw.Draw(src)
    d.ellipse([20, 20, 120, 120], fill=(220, 80, 60))
    d.rectangle([130, 30, 230, 130], fill=(60, 200, 90))
    d.ellipse([50, 140, 150, 240], fill=(40, 60, 200))
    d.rectangle([160, 150, 240, 230], fill=(240, 220, 30))

    # Three byte-different variants: different JPEG quality + a tiny crop.
    src.save(root / "burst_1.jpg", format="JPEG", quality=92)
    src.save(root / "burst_2.jpg", format="JPEG", quality=60)
    src.crop((4, 4, 252, 252)).save(root / "burst_3.jpg", format="JPEG", quality=85)

    # An unrelated image: distinct subject, distant pHash.
    other = Image.new("RGB", (256, 256), (40, 40, 40))
    ImageDraw.Draw(other).rectangle([20, 20, 236, 236], fill=(255, 255, 0))
    other.save(root / "unrelated.jpg", format="JPEG", quality=85)

    return root
