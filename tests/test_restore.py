"""Tests for dedupe.restore."""

from __future__ import annotations

from pathlib import Path

import pytest

from dedupe.restore import RestoreOptions, run_restore
from dedupe.scan import ScanOptions, run_scan
from dedupe.ui import UI, UIConfig

QUIET = UI(UIConfig(quiet=True))


def _scan(fixture_tree: Path) -> Path:
    dups = fixture_tree.parent / "dups"
    run_scan(ScanOptions(source=fixture_tree, dups_folder=dups), QUIET)
    return dups


def test_restore_moves_files_back(fixture_tree: Path):
    dups = _scan(fixture_tree)
    moved_paths = [
        fixture_tree / "subdir" / "dup1_copy.jpg",
        fixture_tree / "deep" / "nested" / "dup1_copy2.jpg",
        fixture_tree / "archive" / "dup2_copy.png",
    ]
    for p in moved_paths:
        assert not p.exists()

    result = run_restore(RestoreOptions(dups_folder=dups), QUIET)
    assert result.files_restored == 3
    assert result.files_skipped == 0
    assert not result.conflicts
    for p in moved_paths:
        assert p.is_file()


def test_restore_refuses_to_overwrite(fixture_tree: Path):
    dups = _scan(fixture_tree)

    # Create a file at one of the original locations to force a conflict.
    blocker = fixture_tree / "subdir" / "dup1_copy.jpg"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not the original")

    result = run_restore(RestoreOptions(dups_folder=dups), QUIET)
    assert result.files_skipped == 1
    assert any("dup1_copy.jpg" in c for c in result.conflicts)
    # Other entries still restored
    assert result.files_restored == 2
    # Blocker untouched
    assert blocker.read_text() == "not the original"
    # Quarantined version still present
    assert (dups / "subdir" / "dup1_copy.jpg").is_file()


def test_restore_missing_manifest_raises(tmp_path: Path):
    empty_dups = tmp_path / "no_manifest"
    empty_dups.mkdir()
    with pytest.raises(FileNotFoundError):
        run_restore(RestoreOptions(dups_folder=empty_dups), QUIET)


def test_restore_missing_dups_folder_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_restore(RestoreOptions(dups_folder=tmp_path / "nope"), QUIET)
