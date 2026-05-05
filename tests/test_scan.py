"""Tests for dedupe.scan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dedupe.manifest import MANIFEST_NAME
from dedupe.scan import (
    IMAGE_EXTENSIONS,
    ScanOptions,
    hash_file,
    iter_image_files,
    pick_keeper,
    run_scan,
)
from dedupe.ui import UI, UIConfig

QUIET = UI(UIConfig(quiet=True))


def test_image_extensions_lowercase_only():
    for ext in IMAGE_EXTENSIONS:
        assert ext.startswith(".")
        assert ext == ext.lower()


def test_hash_file_deterministic(tmp_path: Path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello, world")
    h1 = hash_file(p)
    h2 = hash_file(p)
    assert h1 == h2
    # SHA-256 of "hello, world"
    assert h1 == "09ca7e4eaa6e8ae9c7d261167129184883644d07dfba7cbfbc4c8a2e08360d5b"


def test_pick_keeper_shortest_path():
    paths = [
        Path("/x/aaa/b.jpg"),
        Path("/x/aaa/c.jpg"),
        Path("/x/a.jpg"),  # shortest
    ]
    assert pick_keeper(paths) == Path("/x/a.jpg")


def test_pick_keeper_alphabetical_tiebreak():
    paths = [Path("/aaa/x.jpg"), Path("/aab/x.jpg")]
    # Both lengths equal; alphabetical wins.
    assert pick_keeper(paths) == Path("/aaa/x.jpg")


def test_iter_image_files_skips_hidden_by_default(fixture_tree: Path):
    opts = ScanOptions(source=fixture_tree, dups_folder=fixture_tree.parent / "dups")
    files = sorted(iter_image_files(opts))
    names = {p.name for p in files}
    assert ".hidden_dup.jpg" not in names
    # all expected non-hidden files are present
    assert "unique_a.jpg" in names
    assert "dup1_copy.jpg" in names


def test_iter_image_files_include_hidden(fixture_tree: Path):
    opts = ScanOptions(
        source=fixture_tree,
        dups_folder=fixture_tree.parent / "dups",
        include_hidden=True,
    )
    files = sorted(iter_image_files(opts))
    names = {p.name for p in files}
    assert ".hidden_dup.jpg" in names


def test_run_scan_finds_duplicates_and_moves_them(fixture_tree: Path):
    dups = fixture_tree.parent / "dups"
    opts = ScanOptions(source=fixture_tree, dups_folder=dups)
    result = run_scan(opts, QUIET)

    # Two duplicate groups: blue (3 files) and yellow (2 files).
    assert result.duplicate_groups == 2
    # Blue keeper is dup1.jpg (shortest path, alphabetical-tiebreak); 2 movers.
    # Yellow keeper is dup2.png; 1 mover.
    assert result.files_moved == 3

    # Keeper files still present in source
    assert (fixture_tree / "dup1.jpg").exists()
    assert (fixture_tree / "dup2.png").exists()

    # Movers gone from source
    assert not (fixture_tree / "subdir" / "dup1_copy.jpg").exists()
    assert not (fixture_tree / "deep" / "nested" / "dup1_copy2.jpg").exists()
    assert not (fixture_tree / "archive" / "dup2_copy.png").exists()

    # Mirrored layout under dups
    assert (dups / "subdir" / "dup1_copy.jpg").is_file()
    assert (dups / "deep" / "nested" / "dup1_copy2.jpg").is_file()
    assert (dups / "archive" / "dup2_copy.png").is_file()


def test_run_scan_writes_valid_manifest(fixture_tree: Path):
    dups = fixture_tree.parent / "dups"
    opts = ScanOptions(source=fixture_tree, dups_folder=dups)
    run_scan(opts, QUIET)

    manifest_path = dups / MANIFEST_NAME
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text())
    assert data["version"] == 1
    assert len(data["entries"]) == 3

    for entry in data["entries"]:
        for k in ("original_path", "new_path", "sha256", "kept_path", "size_bytes", "timestamp"):
            assert k in entry
        assert len(entry["sha256"]) == 64


def test_run_scan_dry_run_does_not_move(fixture_tree: Path):
    dups = fixture_tree.parent / "dups"
    opts = ScanOptions(source=fixture_tree, dups_folder=dups, dry_run=True)
    result = run_scan(opts, QUIET)

    assert result.duplicate_groups == 2
    assert result.files_moved == 3  # would-have-moved count

    # Nothing was actually moved
    assert (fixture_tree / "subdir" / "dup1_copy.jpg").exists()
    assert (fixture_tree / "archive" / "dup2_copy.png").exists()
    # Dups folder must not be created in dry-run mode
    assert not dups.exists()


def test_run_scan_no_duplicates_returns_clean_result(tmp_path: Path, make_image):
    src = tmp_path / "uniques"
    make_image(src / "a.jpg", (10, 10, 10))
    make_image(src / "b.jpg", (20, 20, 20))
    make_image(src / "c.jpg", (30, 30, 30))
    dups = tmp_path / "dups"

    result = run_scan(ScanOptions(source=src, dups_folder=dups), QUIET)
    assert result.duplicate_groups == 0
    assert result.files_moved == 0
    assert not dups.exists()


def test_run_scan_recursive_off(fixture_tree: Path):
    dups = fixture_tree.parent / "dups"
    opts = ScanOptions(source=fixture_tree, dups_folder=dups, recursive=False)
    result = run_scan(opts, QUIET)
    # With recursion off, only top-level duplicates are visible.
    # Top-level has dup1.jpg only (its copies are in subdirs); dup2.png only.
    # No duplicate groups at the top level.
    assert result.duplicate_groups == 0


def test_run_scan_missing_source_raises(tmp_path: Path):
    bad = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        run_scan(ScanOptions(source=bad, dups_folder=tmp_path / "d"), QUIET)
