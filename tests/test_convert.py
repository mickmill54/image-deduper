"""Tests for dedupe.convert."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from dedupe.convert import (
    DEFAULT_QUALITY,
    DEFAULT_SOURCE_EXTS,
    ConvertOptions,
    run_convert,
)
from dedupe.ui import UI, UIConfig

QUIET = UI(UIConfig(quiet=True))


def _opts(source: Path, output: Path, **kwargs) -> ConvertOptions:
    """Build ConvertOptions with project-default values, overridable in tests."""
    return ConvertOptions(
        source=source,
        output_folder=output,
        target_format=kwargs.pop("target_format", "jpeg"),
        quality=kwargs.pop("quality", DEFAULT_QUALITY),
        source_exts=kwargs.pop("source_exts", DEFAULT_SOURCE_EXTS),
        **kwargs,
    )


def test_default_source_exts_match_heic_family():
    assert ".heic" in DEFAULT_SOURCE_EXTS
    assert ".heif" in DEFAULT_SOURCE_EXTS


def test_unsupported_target_format_raises(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    opts = _opts(
        convert_tree, out, target_format="bogus", source_exts=frozenset({".jpg"})
    )
    with pytest.raises(ValueError, match="unsupported target format"):
        run_convert(opts, QUIET)


def test_missing_source_raises(tmp_path: Path):
    bad = tmp_path / "missing"
    out = tmp_path / "out"
    opts = _opts(bad, out, source_exts=frozenset({".jpg"}))
    with pytest.raises(FileNotFoundError):
        run_convert(opts, QUIET)


def test_no_eligible_files_returns_clean_result(convert_tree: Path, tmp_path: Path):
    """convert_tree has no .heic; default source_exts shouldn't match anything."""
    out = tmp_path / "out"
    opts = _opts(convert_tree, out)  # default source_exts (.heic, .heif)
    result = run_convert(opts, QUIET)
    assert result.files_scanned == 0
    assert result.files_converted == 0
    assert not out.exists()


def test_convert_jpg_to_png_mirrors_layout(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    opts = _opts(
        convert_tree, out, target_format="png", source_exts=frozenset({".jpg"})
    )
    result = run_convert(opts, QUIET)

    # a.jpg + sub/c.jpg are eligible (hidden file skipped, png/bmp not in source_exts)
    assert result.files_scanned == 2
    assert result.files_converted == 2
    assert result.files_skipped == 0
    assert (out / "a.png").is_file()
    assert (out / "sub" / "c.png").is_file()
    # Originals untouched
    assert (convert_tree / "a.jpg").is_file()
    assert (convert_tree / "sub" / "c.jpg").is_file()


def test_dry_run_writes_nothing(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    opts = _opts(
        convert_tree,
        out,
        target_format="png",
        source_exts=frozenset({".jpg"}),
        dry_run=True,
    )
    result = run_convert(opts, QUIET)
    assert result.files_converted == 2
    # Nothing on disk
    assert not out.exists()


def test_refuses_to_overwrite(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    # Pre-create an output collision
    (out / "a.png").write_bytes(b"existing")

    opts = _opts(
        convert_tree, out, target_format="png", source_exts=frozenset({".jpg"})
    )
    result = run_convert(opts, QUIET)

    # 1 success (sub/c.jpg), 1 skip (a.jpg collision)
    assert result.files_converted == 1
    assert result.files_skipped == 1
    assert any("refusing to overwrite" in e for e in result.errors)
    # Pre-existing file unchanged
    assert (out / "a.png").read_bytes() == b"existing"
    # The non-conflicting one was written
    assert (out / "sub" / "c.png").is_file()


def test_hidden_skipped_by_default(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    opts = _opts(
        convert_tree, out, target_format="png", source_exts=frozenset({".jpg"})
    )
    result = run_convert(opts, QUIET)
    converted_names = {Path(s).name for s, _ in result.conversions}
    assert ".hidden.jpg" not in converted_names


def test_hidden_included_with_flag(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    opts = _opts(
        convert_tree,
        out,
        target_format="png",
        source_exts=frozenset({".jpg"}),
        include_hidden=True,
    )
    result = run_convert(opts, QUIET)
    converted_names = {Path(s).name for s, _ in result.conversions}
    assert ".hidden.jpg" in converted_names


def test_jpeg_output_is_valid_image(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    opts = _opts(
        convert_tree, out, target_format="jpeg", source_exts=frozenset({".png"})
    )
    run_convert(opts, QUIET)
    produced = out / "b.jpg"
    assert produced.is_file()
    with Image.open(produced) as img:
        assert img.format == "JPEG"
        assert img.size == (64, 64)


@pytest.mark.heic
def test_convert_heic_to_jpeg(heic_tree, tmp_path: Path):
    if heic_tree is None:
        pytest.skip("pillow-heif HEIF encoder not available in this build")
    out = tmp_path / "out"
    opts = _opts(heic_tree, out, target_format="jpeg")  # default source_exts
    result = run_convert(opts, QUIET)
    assert result.files_converted == 1
    produced = out / "photo.jpg"
    assert produced.is_file()
    with Image.open(produced) as img:
        assert img.format == "JPEG"
