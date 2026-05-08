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
    assert payload["mode"] == "delete"
    assert payload["files_swept"] == 1
    assert payload["entries"][0]["action"] == "deleted"


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
