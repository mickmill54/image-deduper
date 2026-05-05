"""Tests for dedupe.info."""

from __future__ import annotations

from pathlib import Path

import pytest

from dedupe.info import InfoOptions, run_info
from dedupe.ui import UI, UIConfig

QUIET = UI(UIConfig(quiet=True))


def test_info_missing_source_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_info(InfoOptions(source=tmp_path / "nope"), QUIET)


def test_info_empty_folder(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run_info(InfoOptions(source=empty), QUIET)
    assert result.total_files == 0
    assert result.image_files == 0
    assert result.by_extension == {}


def test_info_counts_extensions(fixture_tree: Path):
    """fixture_tree has .jpg and .png images plus a hidden .jpg."""
    result = run_info(InfoOptions(source=fixture_tree), QUIET)
    assert result.total_files >= 7  # 4 jpg + 2 png + 1 hidden jpg
    assert result.image_files == result.total_files  # everything is an image
    # Hidden file counted but only if include_hidden (default True for info)
    assert result.hidden_files == 1
    # Both extensions appear in the breakdown
    assert ".jpg" in result.by_extension
    assert ".png" in result.by_extension
    # jpg dominates the count
    assert result.by_extension[".jpg"] >= 4
    assert result.by_extension[".png"] == 2


def test_info_exclude_hidden(fixture_tree: Path):
    result = run_info(InfoOptions(source=fixture_tree, include_hidden=False), QUIET)
    # Hidden file count is still tracked, but the file is excluded from totals
    assert result.hidden_files == 1
    # The hidden file (".hidden_dup.jpg") should NOT contribute to total_files
    # (that excludes 1 vs the include-hidden case)
    full = run_info(InfoOptions(source=fixture_tree, include_hidden=True), QUIET)
    assert full.total_files == result.total_files + 1


def test_info_exclude_pattern(fixture_tree: Path):
    result = run_info(InfoOptions(source=fixture_tree, exclude_patterns=("subdir/*",)), QUIET)
    # subdir/dup1_copy.jpg is excluded
    full = run_info(InfoOptions(source=fixture_tree), QUIET)
    assert result.total_files == full.total_files - 1


def test_info_handles_non_image_file(tmp_path: Path, make_image):
    src = tmp_path / "mixed"
    make_image(src / "a.jpg", (200, 30, 30))
    (src / "notes.txt").write_text("not an image")
    result = run_info(InfoOptions(source=src), QUIET)
    assert result.total_files == 2
    assert result.image_files == 1
    assert result.non_image_files == 1
    assert ".txt" in result.by_extension
