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
