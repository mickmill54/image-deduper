"""Tests for dedupe.convert."""

from __future__ import annotations

import json
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
    opts = _opts(convert_tree, out, target_format="bogus", source_exts=frozenset({".jpg"}))
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
    opts = _opts(convert_tree, out, target_format="png", source_exts=frozenset({".jpg"}))
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

    opts = _opts(convert_tree, out, target_format="png", source_exts=frozenset({".jpg"}))
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
    opts = _opts(convert_tree, out, target_format="png", source_exts=frozenset({".jpg"}))
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
    opts = _opts(convert_tree, out, target_format="jpeg", source_exts=frozenset({".png"}))
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


# --- archive-originals tests --------------------------------------------


def test_archive_off_by_default_originals_remain(convert_tree: Path, tmp_path: Path):
    """Sanity check: without --archive-originals, source files are untouched."""
    out = tmp_path / "out"
    opts = _opts(convert_tree, out, target_format="png", source_exts=frozenset({".jpg"}))
    run_convert(opts, QUIET)
    # Source files still present
    assert (convert_tree / "a.jpg").is_file()
    assert (convert_tree / "sub" / "c.jpg").is_file()
    assert opts.archive_folder is None
    # No archive folder created
    assert not (convert_tree.parent / f"{convert_tree.name}-heic").exists()


def test_archive_originals_moves_sources_and_writes_manifest(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    archive = tmp_path / "archive"
    opts = _opts(
        convert_tree,
        out,
        target_format="png",
        source_exts=frozenset({".jpg"}),
        archive_originals=True,
        archive_folder=archive,
    )
    result = run_convert(opts, QUIET)

    assert result.files_converted == 2
    assert result.files_archived == 2

    # Originals gone from source
    assert not (convert_tree / "a.jpg").exists()
    assert not (convert_tree / "sub" / "c.jpg").exists()

    # Originals in archive, mirrored layout
    assert (archive / "a.jpg").is_file()
    assert (archive / "sub" / "c.jpg").is_file()

    # Converted outputs present
    assert (out / "a.png").is_file()
    assert (out / "sub" / "c.png").is_file()

    # Archive manifest valid JSON with the expected fields
    manifest_path = archive / "archive-manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text())
    assert data["version"] == 1
    assert data["target_format"] == "png"
    assert len(data["entries"]) == 2
    for e in data["entries"]:
        for k in (
            "original_path",
            "archive_path",
            "converted_to_path",
            "size_bytes",
            "timestamp",
        ):
            assert k in e


def test_archive_default_folder_is_folder_dash_heic(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    opts = _opts(
        convert_tree,
        out,
        target_format="png",
        source_exts=frozenset({".jpg"}),
        archive_originals=True,
        # archive_folder=None — let it default
    )
    result = run_convert(opts, QUIET)
    assert result.files_archived == 2

    expected_archive = convert_tree.parent / f"{convert_tree.name}-heic"
    assert expected_archive.is_dir()
    assert (expected_archive / "a.jpg").is_file()
    assert (expected_archive / "archive-manifest.json").is_file()


def test_archive_dry_run_moves_nothing(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    archive = tmp_path / "archive"
    opts = _opts(
        convert_tree,
        out,
        target_format="png",
        source_exts=frozenset({".jpg"}),
        archive_originals=True,
        archive_folder=archive,
        dry_run=True,
    )
    result = run_convert(opts, QUIET)
    # files_converted is the planned count in dry-run; archive count stays 0
    # because the archive pass is skipped (only operates on real conversions)
    assert result.files_archived == 0
    assert not archive.exists()
    # Originals untouched
    assert (convert_tree / "a.jpg").is_file()
    assert (convert_tree / "sub" / "c.jpg").is_file()


def test_in_place_via_options_writes_to_source_and_archives(convert_tree: Path, tmp_path: Path):
    """Direct ConvertOptions equivalent of `--in-place`: output_folder == source."""
    archive = tmp_path / "archive"
    opts = _opts(
        convert_tree,
        convert_tree,  # output IS the source folder
        target_format="png",
        source_exts=frozenset({".jpg"}),
        archive_originals=True,
        archive_folder=archive,
    )
    result = run_convert(opts, QUIET)

    assert result.files_converted == 2
    assert result.files_archived == 2

    # Converted files are in the source folder
    assert (convert_tree / "a.png").is_file()
    assert (convert_tree / "sub" / "c.png").is_file()
    # Originals moved out
    assert not (convert_tree / "a.jpg").exists()
    assert not (convert_tree / "sub" / "c.jpg").exists()
    # Originals are in the archive
    assert (archive / "a.jpg").is_file()
    assert (archive / "sub" / "c.jpg").is_file()


def test_convert_exclude_pattern_skips_subdir(convert_tree: Path, tmp_path: Path):
    """convert respects --exclude via the same iter_image_files filter as scan."""
    out = tmp_path / "out"
    opts = _opts(
        convert_tree,
        out,
        target_format="png",
        source_exts=frozenset({".jpg"}),
        exclude_patterns=("sub/*",),
    )
    result = run_convert(opts, QUIET)
    # Only top-level a.jpg converts; sub/c.jpg is excluded.
    assert result.files_converted == 1
    assert (out / "a.png").is_file()
    assert not (out / "sub" / "c.png").exists()


def test_archive_refuses_to_overwrite(convert_tree: Path, tmp_path: Path):
    out = tmp_path / "out"
    archive = tmp_path / "archive"
    archive.mkdir()
    # Pre-create one of the archive destinations
    (archive / "a.jpg").write_bytes(b"existing")

    opts = _opts(
        convert_tree,
        out,
        target_format="png",
        source_exts=frozenset({".jpg"}),
        archive_originals=True,
        archive_folder=archive,
    )
    result = run_convert(opts, QUIET)

    # 1 original was archived (sub/c.jpg); 1 was blocked (a.jpg)
    assert result.files_archived == 1
    assert any("refusing to overwrite archive path" in e for e in result.errors)
    # Pre-existing file untouched
    assert (archive / "a.jpg").read_bytes() == b"existing"
    # Source file that couldn't be archived is still in place
    assert (convert_tree / "a.jpg").is_file()
    # Source file that was archived is gone
    assert not (convert_tree / "sub" / "c.jpg").exists()


# --- --on-conflict modes (#47) ----------------------------------------------
#
# Setup for these tests mirrors a real iPhone library layout: every
# source has a matching destination already in place, so the conflict
# path is exercised for every file. We build the fixture inline rather
# than reusing convert_tree because we want full control over which
# destinations exist and what bytes they hold.


def _build_conflict_tree(root: Path) -> tuple[Path, Path]:
    """Make a source folder with `IMG_001.jpg` + `IMG_002.jpg` and an
    output folder with pre-existing `IMG_001.png` + `IMG_002.png`
    (forcing collisions on conversion to PNG). Returns (src, out)."""
    src = root / "in"
    out = root / "out"
    src.mkdir()
    out.mkdir()
    # Two real (tiny) JPGs as inputs.
    Image.new("RGB", (4, 4), (200, 30, 30)).save(src / "IMG_001.jpg", "JPEG")
    Image.new("RGB", (4, 4), (30, 200, 30)).save(src / "IMG_002.jpg", "JPEG")
    # Pre-existing PNGs at the destinations — these block the convert
    # under default `skip` mode, and the new modes resolve them.
    (out / "IMG_001.png").write_bytes(b"\x89PNG\r\n\x1a\n-existing-1")
    (out / "IMG_002.png").write_bytes(b"\x89PNG\r\n\x1a\n-existing-2")
    return src, out


def test_on_conflict_skip_is_default_and_preserves_v0_12_behavior(tmp_path: Path):
    """Default mode is `skip`: refuse to overwrite, count as files_skipped,
    log a 'refusing to overwrite' error, leave HEIC originals alone.
    Pinned so the v0.13.0 default change can't sneak in unnoticed."""
    src, out = _build_conflict_tree(tmp_path)

    result = run_convert(
        _opts(src, out, target_format="png", source_exts=frozenset({".jpg"})),
        QUIET,
    )
    assert result.files_skipped == 2
    assert result.files_converted == 0
    assert result.files_kept_existing == 0
    assert all("refusing to overwrite" in e for e in result.errors)
    # Pre-existing PNGs untouched.
    assert (out / "IMG_001.png").read_bytes() == b"\x89PNG\r\n\x1a\n-existing-1"
    # Source JPGs untouched (no archive without --archive-originals).
    assert (src / "IMG_001.jpg").is_file()
    assert (src / "IMG_002.jpg").is_file()


def test_on_conflict_archive_anyway_keeps_existing_archives_originals(tmp_path: Path):
    """archive-anyway: don't write a new PNG (existing one is canonical),
    but DO archive the JPG so the source folder ends up clean. This is
    the iPhone HEIC+JPG-pair use case."""
    src, out = _build_conflict_tree(tmp_path)
    archive = tmp_path / "archive"

    result = run_convert(
        _opts(
            src,
            out,
            target_format="png",
            source_exts=frozenset({".jpg"}),
            on_conflict="archive-anyway",
            archive_originals=True,
            archive_folder=archive,
        ),
        QUIET,
    )

    # Both files were "kept existing" — no new PNGs written.
    assert result.files_kept_existing == 2
    assert result.files_converted == 0
    assert result.bytes_written == 0
    assert result.files_skipped == 0
    assert not result.errors

    # Pre-existing PNGs unchanged.
    assert (out / "IMG_001.png").read_bytes() == b"\x89PNG\r\n\x1a\n-existing-1"
    assert (out / "IMG_002.png").read_bytes() == b"\x89PNG\r\n\x1a\n-existing-2"

    # Originals were archived — source is now clean.
    assert result.files_archived == 2
    assert (archive / "IMG_001.jpg").is_file()
    assert (archive / "IMG_002.jpg").is_file()
    assert not (src / "IMG_001.jpg").exists()
    assert not (src / "IMG_002.jpg").exists()


def test_on_conflict_archive_anyway_no_archive_skips_only(tmp_path: Path):
    """archive-anyway WITHOUT --archive-originals: keep_existing path runs
    (no errors, no new PNGs written) but the originals stay in source
    because there's no archive pass enabled. Edge case worth pinning so
    nobody accidentally couples archive-anyway to archive_originals=True."""
    src, out = _build_conflict_tree(tmp_path)

    result = run_convert(
        _opts(
            src,
            out,
            target_format="png",
            source_exts=frozenset({".jpg"}),
            on_conflict="archive-anyway",
            archive_originals=False,
        ),
        QUIET,
    )
    assert result.files_kept_existing == 2
    assert result.files_archived == 0
    assert not result.errors
    # Originals still in source because no archive was requested.
    assert (src / "IMG_001.jpg").is_file()
    assert (src / "IMG_002.jpg").is_file()


def test_on_conflict_number_writes_suffixed_variant(tmp_path: Path):
    """number: writes IMG_001-1.png, IMG_002-1.png, both files coexist."""
    src, out = _build_conflict_tree(tmp_path)

    result = run_convert(
        _opts(
            src,
            out,
            target_format="png",
            source_exts=frozenset({".jpg"}),
            on_conflict="number",
        ),
        QUIET,
    )

    assert result.files_converted == 2
    assert result.files_numbered == 2
    assert result.files_kept_existing == 0
    assert not result.errors

    # Both pre-existing PNGs untouched + numbered variants written.
    assert (out / "IMG_001.png").read_bytes() == b"\x89PNG\r\n\x1a\n-existing-1"
    assert (out / "IMG_001-1.png").is_file()
    assert (out / "IMG_002.png").read_bytes() == b"\x89PNG\r\n\x1a\n-existing-2"
    assert (out / "IMG_002-1.png").is_file()
    # The numbered-output bytes are real PNG (not the placeholder).
    assert (out / "IMG_001-1.png").read_bytes().startswith(b"\x89PNG")


def test_on_conflict_number_finds_lowest_unused_suffix(tmp_path: Path):
    """If IMG_001-1.png is also taken, number tries -2, -3, ..."""
    src, out = _build_conflict_tree(tmp_path)
    # Pre-occupy -1 and -2 to force the writer to pick -3.
    (out / "IMG_001-1.png").write_bytes(b"taken-1")
    (out / "IMG_001-2.png").write_bytes(b"taken-2")

    result = run_convert(
        _opts(
            src,
            out,
            target_format="png",
            source_exts=frozenset({".jpg"}),
            on_conflict="number",
        ),
        QUIET,
    )
    assert (out / "IMG_001-3.png").is_file()
    # The pre-existing -1 and -2 still hold the placeholder bytes.
    assert (out / "IMG_001-1.png").read_bytes() == b"taken-1"
    assert (out / "IMG_001-2.png").read_bytes() == b"taken-2"
    assert result.files_numbered == 2  # IMG_001 + IMG_002 both got numbered


def test_on_conflict_overwrite_replaces_existing(tmp_path: Path):
    """overwrite: existing PNG replaced with the freshly-encoded one."""
    src, out = _build_conflict_tree(tmp_path)

    result = run_convert(
        _opts(
            src,
            out,
            target_format="png",
            source_exts=frozenset({".jpg"}),
            on_conflict="overwrite",
        ),
        QUIET,
    )

    assert result.files_converted == 2
    assert result.files_overwritten == 2
    assert result.files_kept_existing == 0
    assert result.files_numbered == 0
    assert not result.errors

    # Pre-existing placeholder bytes have been replaced — file now starts
    # with PNG magic AND is bigger than the placeholder (which was 16
    # bytes; a 4×4 PNG is hundreds of bytes).
    a = (out / "IMG_001.png").read_bytes()
    assert a.startswith(b"\x89PNG")
    assert len(a) > 16


def test_on_conflict_dry_run_archive_anyway_records_outcome_no_writes(tmp_path: Path):
    """archive-anyway + dry_run: counters update, but nothing on disk
    changes (no archive moves, no JPG write)."""
    src, out = _build_conflict_tree(tmp_path)
    archive = tmp_path / "archive"

    result = run_convert(
        _opts(
            src,
            out,
            target_format="png",
            source_exts=frozenset({".jpg"}),
            on_conflict="archive-anyway",
            archive_originals=True,
            archive_folder=archive,
            dry_run=True,
        ),
        QUIET,
    )

    assert result.files_kept_existing == 2
    assert result.files_archived == 0  # no physical archive in dry-run
    # Source originals untouched.
    assert (src / "IMG_001.jpg").is_file()
    assert (src / "IMG_002.jpg").is_file()
    # Archive folder may or may not exist; it shouldn't contain anything.
    if archive.exists():
        assert not list(archive.glob("*.jpg"))


def test_on_conflict_invalid_mode_raises_via_options(tmp_path: Path):
    """Defensive: an unknown mode raises rather than silently doing
    something surprising. The CLI's argparse `choices=` blocks this at
    the user boundary; this test pins the library-level guard."""
    src, out = _build_conflict_tree(tmp_path)

    result = run_convert(
        _opts(
            src,
            out,
            target_format="png",
            source_exts=frozenset({".jpg"}),
            on_conflict="bogus-mode",
        ),
        QUIET,
    )
    # The mode is invalid but the conflict is still resolved per the
    # ValueError fallback in _convert_one — every file becomes an error.
    assert all("unknown on_conflict mode" in e for e in result.errors)
    assert result.files_converted == 0
