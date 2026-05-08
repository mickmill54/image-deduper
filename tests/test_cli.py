"""End-to-end tests for the dedupe CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dedupe.cli import main


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "dedupe" in out


def test_cli_no_args_prints_help_and_returns_2(capsys):
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 2
    assert "usage:" in captured.out


def test_cli_bad_subcommand_returns_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["nonsense"])
    assert exc.value.code == 2


def test_cli_scan_missing_folder_returns_1(tmp_path: Path):
    rc = main(["scan", str(tmp_path / "does_not_exist"), "--quiet"])
    assert rc == 1


def test_cli_scan_dry_run_makes_no_changes(fixture_tree: Path):
    rc = main(["scan", str(fixture_tree), "--dry-run", "--quiet"])
    assert rc == 0
    # Nothing moved
    assert (fixture_tree / "subdir" / "dup1_copy.jpg").exists()
    # No dups folder created
    assert not (fixture_tree.parent / f"{fixture_tree.name}-dups").exists()


def test_cli_scan_end_to_end(fixture_tree: Path):
    rc = main(["scan", str(fixture_tree), "--quiet"])
    assert rc == 0
    dups = fixture_tree.parent / f"{fixture_tree.name}-dups"
    assert dups.is_dir()
    assert (dups / "manifest.json").is_file()


def test_cli_scan_json_mode(fixture_tree: Path, capsys):
    rc = main(["scan", str(fixture_tree), "--json", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["command"] == "scan"
    assert payload["dry_run"] is True
    assert payload["duplicate_groups"] == 2
    assert payload["files_moved"] == 3


def test_cli_restore_round_trip(fixture_tree: Path):
    dups = fixture_tree.parent / f"{fixture_tree.name}-dups"
    assert main(["scan", str(fixture_tree), "--quiet"]) == 0
    assert main(["restore", str(dups), "--quiet"]) == 0
    # Files put back
    assert (fixture_tree / "subdir" / "dup1_copy.jpg").is_file()
    assert (fixture_tree / "archive" / "dup2_copy.png").is_file()


def test_cli_restore_sweep_videos_round_trip(tmp_path: Path):
    """End-to-end via the CLI: sweep --videos then restore the videos
    folder reverses every move (#42)."""
    src = tmp_path / "Photos"
    src.mkdir()
    (src / "trip.mov").write_bytes(b"v")
    (src / "photo.jpg").write_bytes(b"\xff\xd8")
    (src / "2024").mkdir()
    (src / "2024" / "ski.mp4").write_bytes(b"v")

    assert main(["sweep", str(src), "--videos", "--quiet"]) == 0
    videos_folder = src.parent / f"{src.name} - videos"
    assert (videos_folder / "trip.mov").is_file()

    assert main(["restore", str(videos_folder), "--quiet"]) == 0
    # Videos back at original paths
    assert (src / "trip.mov").is_file()
    assert (src / "2024" / "ski.mp4").is_file()
    # Image untouched throughout
    assert (src / "photo.jpg").is_file()


def test_cli_restore_sweep_junk_log_reports_no_errors(tmp_path: Path):
    """Restore on a junk-delete audit log exits cleanly and reports
    the deletes as one-way (not as errors)."""
    src = tmp_path / "Photos"
    src.mkdir()
    (src / "Thumbs.db").write_text("cache")

    assert main(["sweep", str(src), "--junk", "--quiet"]) == 0
    log_folder = src.parent / f"{src.name}-sweep-log"

    # JSON mode lets us assert the structured output.
    rc = main(["restore", str(log_folder), "--json"])
    assert rc == 0


def test_cli_restore_json_output_includes_manifest_kind(tmp_path: Path, capsys):
    """JSON output declares the manifest type so callers know which
    code path executed."""
    src = tmp_path / "Photos"
    src.mkdir()
    (src / "trip.mov").write_bytes(b"v")

    assert main(["sweep", str(src), "--videos", "--quiet"]) == 0
    capsys.readouterr()  # drain sweep output
    videos_folder = src.parent / f"{src.name} - videos"

    rc = main(["restore", str(videos_folder), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "restore"
    assert payload["manifest_kind"] == "sweep"
    assert payload["files_restored"] == 1


def test_cli_convert_in_place_end_to_end(convert_tree: Path):
    rc = main(
        [
            "convert",
            str(convert_tree),
            "--in-place",
            "--to",
            "png",
            "--source-ext",
            "jpg",
            "--quiet",
        ]
    )
    assert rc == 0
    # Converted PNGs in the source folder
    assert (convert_tree / "a.png").is_file()
    assert (convert_tree / "sub" / "c.png").is_file()
    # Originals gone from source
    assert not (convert_tree / "a.jpg").exists()
    assert not (convert_tree / "sub" / "c.jpg").exists()
    # Default archive folder created with a manifest
    archive = convert_tree.parent / f"{convert_tree.name}-heic"
    assert (archive / "a.jpg").is_file()
    assert (archive / "archive-manifest.json").is_file()


def test_cli_convert_in_place_conflicts_with_output_folder(convert_tree: Path, tmp_path: Path):
    rc = main(
        [
            "convert",
            str(convert_tree),
            "--in-place",
            "--output-folder",
            str(tmp_path / "elsewhere"),
            "--quiet",
        ]
    )
    # Should refuse with a usage-style exit code
    assert rc == 2


def test_cli_convert_from_any_excludes_target_format(convert_tree: Path, tmp_path: Path):
    """--from-any grabs png/bmp but skips existing JPGs when target is jpeg."""
    out = tmp_path / "out"
    rc = main(
        [
            "convert",
            str(convert_tree),
            "--from-any",
            "--to",
            "jpeg",
            "--output-folder",
            str(out),
            "--quiet",
        ]
    )
    assert rc == 0
    # b.png and sub/d.bmp should convert
    assert (out / "b.jpg").is_file()
    assert (out / "sub" / "d.jpg").is_file()
    # Existing JPGs not re-encoded
    assert not (out / "a.jpg").exists()


def test_cli_convert_from_any_conflicts_with_source_ext(convert_tree: Path):
    rc = main(
        [
            "convert",
            str(convert_tree),
            "--from-any",
            "--source-ext",
            "png",
            "--quiet",
        ]
    )
    assert rc == 2  # EXIT_USAGE


def test_cli_convert_source_ext_comma_list(convert_tree: Path, tmp_path: Path):
    """--source-ext png,bmp == --source-ext png --source-ext bmp."""
    out = tmp_path / "out"
    rc = main(
        [
            "convert",
            str(convert_tree),
            "--source-ext",
            "png,bmp",
            "--to",
            "jpeg",
            "--output-folder",
            str(out),
            "--quiet",
        ]
    )
    assert rc == 0
    assert (out / "b.jpg").is_file()
    assert (out / "sub" / "d.jpg").is_file()


def test_cli_info_human_output(fixture_tree: Path, capsys):
    rc = main(["info", str(fixture_tree)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Summary" in out
    assert "By extension" in out


def test_cli_info_json_output(fixture_tree: Path, capsys):
    rc = main(["info", str(fixture_tree), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "info"
    assert payload["total_files"] >= 7
    assert ".jpg" in payload["by_extension"]


def test_cli_scan_exclude_flag_comma_list(fixture_tree: Path):
    """--exclude accepts comma-separated patterns just like --source-ext."""
    rc = main(
        [
            "scan",
            str(fixture_tree),
            "--exclude",
            "subdir/*,archive/*",
            "--quiet",
        ]
    )
    assert rc == 0
    # Both excluded directories preserved
    assert (fixture_tree / "subdir" / "dup1_copy.jpg").exists()
    assert (fixture_tree / "archive" / "dup2_copy.png").exists()


def test_cli_sweep_junk_default_deletes_with_manifest(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir()
    (src / "Thumbs.db").write_text("cache")
    (src / "sub").mkdir()
    (src / "sub" / ".DS_Store").write_text("meta")
    (src / "keep.jpg").write_bytes(b"\xff\xd8")

    rc = main(["sweep", str(src), "--junk", "--quiet"])
    assert rc == 0

    # Junk gone, real file preserved.
    assert not (src / "Thumbs.db").exists()
    assert not (src / "sub" / ".DS_Store").exists()
    assert (src / "keep.jpg").exists()

    # Default log folder created.
    log_folder = src.parent / f"{src.name}-sweep-log"
    assert (log_folder / "sweep-manifest.json").is_file()


def test_cli_sweep_quarantine_junk_mirrors_layout(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir()
    (src / "Thumbs.db").write_text("a")
    (src / "sub").mkdir()
    (src / "sub" / "Thumbs.db").write_text("b")  # same name, different parent

    rc = main(["sweep", str(src), "--junk", "--quarantine-junk", "--quiet"])
    assert rc == 0

    quarantine = src.parent / f"{src.name}-junk"
    # Both Thumbs.db files coexist in the mirrored quarantine.
    assert (quarantine / "Thumbs.db").is_file()
    assert (quarantine / "sub" / "Thumbs.db").is_file()
    assert (quarantine / "sweep-manifest.json").is_file()


def test_cli_sweep_dry_run(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir()
    (src / "Thumbs.db").write_text("cache")

    rc = main(["sweep", str(src), "--junk", "--dry-run", "--quiet"])
    assert rc == 0
    # Nothing changed.
    assert (src / "Thumbs.db").exists()
    assert not (src.parent / f"{src.name}-sweep-log").exists()


def test_cli_sweep_json_output(tmp_path: Path, capsys):
    src = tmp_path / "photos"
    src.mkdir()
    (src / "Thumbs.db").write_text("cache")

    rc = main(["sweep", str(src), "--junk", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "sweep"
    assert payload["junk_mode"] == "delete"
    assert payload["files_swept"] == 1
    assert payload["junk_swept"] == 1
    assert payload["entries"][0]["action"] == "deleted"


def test_cli_sweep_videos_default_dash_videos_destination(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir()
    (src / "trip.mov").write_bytes(b"video")
    (src / "keep.jpg").write_bytes(b"\xff\xd8")

    rc = main(["sweep", str(src), "--videos", "--quiet"])
    assert rc == 0

    # Default destination is `<src> - videos`; root files land directly
    # inside the wrapper (no extra subfolder).
    expected = src.parent / f"{src.name} - videos"
    assert (expected / "trip.mov").is_file()
    assert (expected / "sweep-manifest.json").is_file()
    # Image preserved
    assert (src / "keep.jpg").exists()


def test_cli_sweep_videos_subdir_layout(tmp_path: Path):
    """End-to-end: a year-folder full of videos lands in `<year> - videos/`
    inside the wrapper, with the basename intact."""
    src = tmp_path / "Photos"
    src.mkdir()
    year = src / "2008 - iPhone"
    year.mkdir()
    (year / "movie.mov").write_bytes(b"v")
    (year / "clip.mp4").write_bytes(b"v")
    nested = src / "2009 - iPhone" / "archive"
    nested.mkdir(parents=True)
    (nested / "old.mov").write_bytes(b"v")
    (src / "photo.jpg").write_bytes(b"\xff\xd8")

    rc = main(["sweep", str(src), "--videos", "--quiet"])
    assert rc == 0

    wrapper = src.parent / f"{src.name} - videos"
    assert (wrapper / "2008 - iPhone - videos" / "movie.mov").is_file()
    assert (wrapper / "2008 - iPhone - videos" / "clip.mp4").is_file()
    assert (wrapper / "2009 - iPhone - videos" / "archive - videos" / "old.mov").is_file()
    # Un-suffixed mirror paths must NOT exist.
    assert not (wrapper / "2008 - iPhone").exists()
    assert not (wrapper / "2009 - iPhone").exists()
    # Image preserved at source.
    assert (src / "photo.jpg").exists()


def test_cli_sweep_non_images_moves_user_content(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir()
    (src / "notes.txt").write_text("notes")
    (src / "manual.pdf").write_bytes(b"%PDF-")
    (src / "keep.jpg").write_bytes(b"\xff\xd8")

    rc = main(["sweep", str(src), "--non-images", "--quiet"])
    assert rc == 0

    expected = src.parent / f"{src.name}-non-images"
    assert (expected / "notes.txt").is_file()
    assert (expected / "manual.pdf").is_file()
    # Image preserved
    assert (src / "keep.jpg").exists()


def test_cli_sweep_combined_modes(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir()
    (src / "Thumbs.db").write_text("cache")
    (src / "notes.txt").write_text("notes")
    (src / "trip.mov").write_bytes(b"video")
    (src / "photo.jpg").write_bytes(b"\xff\xd8")

    rc = main(
        [
            "sweep",
            str(src),
            "--junk",
            "--non-images",
            "--videos",
            "--quiet",
        ]
    )
    assert rc == 0

    # Each category went to its own folder
    assert (src.parent / f"{src.name}-non-images" / "notes.txt").is_file()
    assert (src.parent / f"{src.name} - videos" / "trip.mov").is_file()
    # Junk deleted (default), audit log written
    assert (src.parent / f"{src.name}-sweep-log" / "sweep-manifest.json").is_file()
    assert not (src / "Thumbs.db").exists()
    # Image preserved
    assert (src / "photo.jpg").exists()


# --- regression: dot-source path resolution (#43) --------------------------
#
# When the user runs `dedupe <cmd> .` from inside a folder, the source must
# resolve to absolute before the subcommand computes its sibling destinations.
# Otherwise `Path(".").parent == Path(".")` and `Path(".").name == ""` collapse
# `src.parent / f"{src.name}-suffix"` into a relative path that lands the
# destination INSIDE the source folder. These tests pin that down for every
# subcommand that derives a sibling-of-source destination.


def test_cli_scan_dot_source_lands_dups_as_sibling(tmp_path: Path, monkeypatch):
    src = tmp_path / "Photos"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"\xff\xd8aaa")
    (src / "a_copy.jpg").write_bytes(b"\xff\xd8aaa")  # dup of a.jpg
    monkeypatch.chdir(src)

    rc = main(["scan", ".", "--quiet"])
    assert rc == 0

    # Sibling, not inside
    assert (tmp_path / "Photos-dups").is_dir()
    assert not (src / "Photos-dups").exists()
    assert not (src / "-dups").exists()


def test_cli_sweep_dot_source_lands_destinations_as_siblings(tmp_path: Path, monkeypatch):
    src = tmp_path / "Photos"
    src.mkdir()
    (src / "trip.mov").write_bytes(b"v")
    (src / "notes.txt").write_text("notes")
    (src / "Thumbs.db").write_text("cache")
    monkeypatch.chdir(src)

    rc = main(["sweep", ".", "--junk", "--non-images", "--videos", "--quiet"])
    assert rc == 0

    # All three destinations are siblings of the source
    assert (tmp_path / "Photos - videos" / "trip.mov").is_file()
    assert (tmp_path / "Photos-non-images" / "notes.txt").is_file()
    assert (tmp_path / "Photos-sweep-log" / "sweep-manifest.json").is_file()

    # And NOT inside the source (the bug from #43)
    assert not (src / " - videos").exists()
    assert not (src / "-non-images").exists()
    assert not (src / "-sweep-log").exists()


def test_cli_convert_dot_source_lands_output_as_sibling(tmp_path: Path, monkeypatch):
    src = tmp_path / "Photos"
    src.mkdir()
    # Use a tiny real JPG so the convert pipeline has something to chew.
    from PIL import Image  # noqa: PLC0415

    Image.new("RGB", (4, 4), (200, 30, 30)).save(src / "a.jpg", "JPEG")
    monkeypatch.chdir(src)

    rc = main(["convert", ".", "--source-ext", "jpg", "--to", "png", "--quiet"])
    assert rc == 0

    # Default output is `<folder>-converted` as a sibling
    assert (tmp_path / "Photos-converted" / "a.png").is_file()
    assert not (src / "Photos-converted").exists()
    assert not (src / "-converted").exists()


def test_cli_find_similar_report_only(similar_tree: Path, tmp_path: Path):
    report = tmp_path / "r.html"
    rc = main(
        [
            "find-similar",
            str(similar_tree),
            "--report",
            str(report),
            "--threshold",
            "10",
            "--quiet",
        ]
    )
    assert rc == 0
    assert report.is_file()
    # Folder contents unchanged
    assert sorted(p.name for p in similar_tree.iterdir()) == sorted(
        ["burst_1.jpg", "burst_2.jpg", "burst_3.jpg", "unrelated.jpg"]
    )
