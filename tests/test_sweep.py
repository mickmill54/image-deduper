"""Tests for dedupe.sweep."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dedupe.sweep import (
    ACTION_DELETED,
    ACTION_MOVED,
    JUNK_FILES,
    SweepOptions,
    is_junk_file,
    run_sweep,
)
from dedupe.ui import UI, UIConfig

QUIET = UI(UIConfig(quiet=True))


def _build_junk_tree(root: Path) -> Path:
    """Layout used by most sweep tests:

    root/
      Thumbs.db                 (top-level junk)
      keep.jpg                  (real file, preserved)
      2024/
        Thumbs.db               (collides by name with top-level)
        photo.jpg               (preserved)
      2025/
        .DS_Store
        birthday/
          Thumbs.db
          .DS_Store
          cake.jpg              (preserved)
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "Thumbs.db").write_text("win-thumbnail-cache")
    (root / "keep.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (root / "2024").mkdir()
    (root / "2024" / "Thumbs.db").write_text("more-cache")
    (root / "2024" / "photo.jpg").write_bytes(b"\xff\xd8fake")
    (root / "2025").mkdir()
    (root / "2025" / ".DS_Store").write_text("macos-meta")
    (root / "2025" / "birthday").mkdir()
    (root / "2025" / "birthday" / "Thumbs.db").write_text("yet more cache")
    (root / "2025" / "birthday" / ".DS_Store").write_text("more macos")
    (root / "2025" / "birthday" / "cake.jpg").write_bytes(b"\xff\xd8cake")
    return root


@pytest.fixture
def junk_tree(tmp_path: Path) -> Path:
    return _build_junk_tree(tmp_path / "photos")


# --- core helpers -----------------------------------------------------------


def test_junk_files_constant_contents():
    # Spot-check the documented allowlist.
    assert "Thumbs.db" in JUNK_FILES
    assert ".DS_Store" in JUNK_FILES
    assert "desktop.ini" in JUNK_FILES
    assert ".AppleDouble" in JUNK_FILES


def test_is_junk_file_matches_basename(tmp_path: Path):
    assert is_junk_file(tmp_path / "Thumbs.db")
    assert is_junk_file(tmp_path / "anywhere/.DS_Store")
    assert not is_junk_file(tmp_path / "photo.jpg")
    # Case-sensitive by design (Windows is case-insensitive in practice;
    # we err toward false-negative-on-case rather than aggressive deletion).
    assert not is_junk_file(tmp_path / "thumbs.db")


# --- bail-without-mode ------------------------------------------------------


def test_run_sweep_no_mode_is_no_op(junk_tree: Path):
    result = run_sweep(SweepOptions(source=junk_tree), QUIET)
    assert result.files_swept == 0
    assert result.entries == []
    # Nothing on disk changed.
    assert (junk_tree / "Thumbs.db").exists()
    assert (junk_tree / "2024" / "Thumbs.db").exists()


def test_missing_source_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_sweep(SweepOptions(source=tmp_path / "nope", sweep_junk=True), QUIET)


# --- delete mode (default) --------------------------------------------------


def test_delete_mode_removes_junk_writes_manifest(junk_tree: Path, tmp_path: Path):
    log_folder = tmp_path / "swept-log"
    result = run_sweep(
        SweepOptions(source=junk_tree, sweep_junk=True, log_folder=log_folder),
        QUIET,
    )
    # 4 Thumbs.db + 2 .DS_Store = 6 junk files; in our fixture we have 5.
    # Verify against the actual fixture.
    assert result.files_swept == 5

    # Junk files are gone from source.
    assert not (junk_tree / "Thumbs.db").exists()
    assert not (junk_tree / "2024" / "Thumbs.db").exists()
    assert not (junk_tree / "2025" / ".DS_Store").exists()
    assert not (junk_tree / "2025" / "birthday" / "Thumbs.db").exists()
    assert not (junk_tree / "2025" / "birthday" / ".DS_Store").exists()

    # Real files preserved.
    assert (junk_tree / "keep.jpg").exists()
    assert (junk_tree / "2024" / "photo.jpg").exists()
    assert (junk_tree / "2025" / "birthday" / "cake.jpg").exists()

    # Manifest written.
    manifest_path = log_folder / "sweep-manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text())
    assert data["version"] == 1
    assert data["mode"] == "delete"
    assert data["quarantine_folder"] is None
    assert len(data["entries"]) == 5
    for e in data["entries"]:
        assert e["action"] == ACTION_DELETED
        assert e["new_path"] is None


def test_dry_run_makes_no_changes(junk_tree: Path, tmp_path: Path):
    log_folder = tmp_path / "swept-log"
    result = run_sweep(
        SweepOptions(
            source=junk_tree,
            sweep_junk=True,
            dry_run=True,
            log_folder=log_folder,
        ),
        QUIET,
    )
    # Counted what would be swept...
    assert result.files_swept == 5
    # ...but nothing actually moved or got deleted.
    assert (junk_tree / "Thumbs.db").exists()
    assert (junk_tree / "2025" / "birthday" / ".DS_Store").exists()
    # Log folder NOT created in dry-run.
    assert not log_folder.exists()


def test_idempotent_rerun_is_no_op(junk_tree: Path, tmp_path: Path):
    log_folder = tmp_path / "swept-log"
    first = run_sweep(
        SweepOptions(source=junk_tree, sweep_junk=True, log_folder=log_folder),
        QUIET,
    )
    assert first.files_swept == 5

    # Second run: nothing left to do.
    second = run_sweep(
        SweepOptions(source=junk_tree, sweep_junk=True, log_folder=log_folder),
        QUIET,
    )
    assert second.files_swept == 0
    assert second.errors == []


def test_default_log_folder_is_folder_dash_sweep_log(junk_tree: Path):
    result = run_sweep(SweepOptions(source=junk_tree, sweep_junk=True), QUIET)
    assert result.files_swept == 5
    expected_log_folder = junk_tree.parent / f"{junk_tree.name}-sweep-log"
    assert expected_log_folder.is_dir()
    assert (expected_log_folder / "sweep-manifest.json").is_file()


# --- quarantine mode --------------------------------------------------------


def test_quarantine_mode_mirrors_layout(junk_tree: Path, tmp_path: Path):
    junk_folder = tmp_path / "quarantine"
    result = run_sweep(
        SweepOptions(
            source=junk_tree,
            sweep_junk=True,
            quarantine_junk=True,
            junk_folder=junk_folder,
        ),
        QUIET,
    )
    assert result.files_swept == 5

    # Junk files are gone from source.
    assert not (junk_tree / "Thumbs.db").exists()
    assert not (junk_tree / "2024" / "Thumbs.db").exists()

    # Junk files appear in quarantine, with mirrored paths so identically
    # named files don't collide.
    assert (junk_folder / "Thumbs.db").is_file()
    assert (junk_folder / "2024" / "Thumbs.db").is_file()
    assert (junk_folder / "2025" / ".DS_Store").is_file()
    assert (junk_folder / "2025" / "birthday" / "Thumbs.db").is_file()
    assert (junk_folder / "2025" / "birthday" / ".DS_Store").is_file()

    # Real files still in source.
    assert (junk_tree / "keep.jpg").exists()

    # Manifest sits alongside the quarantined files.
    manifest_path = junk_folder / "sweep-manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text())
    assert data["mode"] == "quarantine"
    assert data["quarantine_folder"] == str(junk_folder.resolve())
    assert all(e["action"] == ACTION_MOVED for e in data["entries"])
    assert all(e["new_path"] is not None for e in data["entries"])


def test_quarantine_default_folder_is_folder_dash_junk(junk_tree: Path):
    result = run_sweep(
        SweepOptions(source=junk_tree, sweep_junk=True, quarantine_junk=True),
        QUIET,
    )
    assert result.files_swept == 5
    expected = junk_tree.parent / f"{junk_tree.name}-junk"
    assert expected.is_dir()
    assert (expected / "sweep-manifest.json").is_file()


def test_quarantine_refuses_to_overwrite(junk_tree: Path, tmp_path: Path):
    junk_folder = tmp_path / "quarantine"
    junk_folder.mkdir()
    (junk_folder / "2024").mkdir()
    # Pre-existing file at one of the destinations.
    (junk_folder / "2024" / "Thumbs.db").write_text("dont-clobber-me")

    result = run_sweep(
        SweepOptions(
            source=junk_tree,
            sweep_junk=True,
            quarantine_junk=True,
            junk_folder=junk_folder,
        ),
        QUIET,
    )
    # 5 junk files - 1 conflict = 4 succeeded
    assert result.files_swept == 4
    assert any("refusing to overwrite" in e for e in result.errors)
    # Pre-existing file untouched.
    assert (junk_folder / "2024" / "Thumbs.db").read_text() == "dont-clobber-me"
    # Source file that couldn't be moved is still in place.
    assert (junk_tree / "2024" / "Thumbs.db").exists()


# --- exclude flag -----------------------------------------------------------


def test_exclude_pattern_skips_subdir(junk_tree: Path, tmp_path: Path):
    log_folder = tmp_path / "swept-log"
    result = run_sweep(
        SweepOptions(
            source=junk_tree,
            sweep_junk=True,
            log_folder=log_folder,
            exclude_patterns=("2025/*",),
        ),
        QUIET,
    )
    # Top-level Thumbs.db + 2024/Thumbs.db = 2 swept; 3 in 2025/ excluded.
    assert result.files_swept == 2
    assert not (junk_tree / "Thumbs.db").exists()
    assert not (junk_tree / "2024" / "Thumbs.db").exists()
    # 2025/* preserved.
    assert (junk_tree / "2025" / ".DS_Store").exists()
    assert (junk_tree / "2025" / "birthday" / "Thumbs.db").exists()


def test_no_recursive_only_top_level(junk_tree: Path, tmp_path: Path):
    log_folder = tmp_path / "swept-log"
    result = run_sweep(
        SweepOptions(
            source=junk_tree,
            sweep_junk=True,
            recursive=False,
            log_folder=log_folder,
        ),
        QUIET,
    )
    # Only the top-level Thumbs.db is found.
    assert result.files_swept == 1
    assert not (junk_tree / "Thumbs.db").exists()
    # Subdir junk preserved.
    assert (junk_tree / "2024" / "Thumbs.db").exists()


# --- empty-tree case --------------------------------------------------------


def test_clean_folder_returns_zero(tmp_path: Path):
    src = tmp_path / "clean"
    src.mkdir()
    (src / "photo.jpg").write_bytes(b"\xff\xd8")
    result = run_sweep(SweepOptions(source=src, sweep_junk=True), QUIET)
    assert result.files_swept == 0
    assert (src / "photo.jpg").exists()
    # No log folder created when there's nothing to log.
    # (We only `mkdir` the log folder right before opening the manifest writer,
    # which we don't do if junk_files is empty.)
    assert not (src.parent / f"{src.name}-sweep-log").exists()
