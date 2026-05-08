"""Tests for dedupe.restore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dedupe.restore import RestoreOptions, run_restore
from dedupe.scan import ScanOptions, run_scan
from dedupe.sweep import SweepOptions, run_sweep
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


# --- sweep restore round-trips (#42) ----------------------------------------


def _build_sweep_tree(tmp_path: Path) -> Path:
    """A minimal source layout with one file per sweep category, used by
    the sweep-restore round-trip tests below. Kept small so each test
    can read it at a glance."""
    src = tmp_path / "Photos"
    src.mkdir()
    (src / "photo.jpg").write_bytes(b"\xff\xd8")  # image, never touched
    (src / "trip.mov").write_bytes(b"video-bytes")  # video
    (src / "notes.txt").write_text("notes")  # non-image
    (src / "Thumbs.db").write_text("cache")  # junk (deleted by default)
    sub = src / "2024"
    sub.mkdir()
    (sub / "ski.mp4").write_bytes(b"video2-bytes")  # video in subdir
    (sub / "manual.pdf").write_bytes(b"%PDF-")  # non-image in subdir
    return src


def test_sweep_videos_restore_round_trip(tmp_path: Path):
    """Sweep --videos then restore puts every video back at its original
    path and preserves images in source."""
    src = _build_sweep_tree(tmp_path)
    run_sweep(SweepOptions(source=src, sweep_videos=True), QUIET)

    videos_folder = src.parent / f"{src.name} - videos"
    assert (videos_folder / "trip.mov").is_file()  # sanity: sweep landed
    assert (videos_folder / "2024 - videos" / "ski.mp4").is_file()
    assert not (src / "trip.mov").exists()
    assert not (src / "2024" / "ski.mp4").exists()

    result = run_restore(RestoreOptions(dups_folder=videos_folder), QUIET)
    assert result.manifest_kind == "sweep"
    assert result.files_restored == 2
    assert result.files_skipped == 0
    assert result.deleted_entries == 0
    assert not result.conflicts
    assert not result.errors

    # Both videos back at original paths.
    assert (src / "trip.mov").is_file()
    assert (src / "2024" / "ski.mp4").is_file()
    # Image untouched throughout.
    assert (src / "photo.jpg").is_file()


def test_sweep_non_images_restore_round_trip(tmp_path: Path):
    """Sweep --non-images then restore puts every non-image back."""
    src = _build_sweep_tree(tmp_path)
    run_sweep(SweepOptions(source=src, sweep_non_images=True), QUIET)

    non_images = src.parent / f"{src.name}-non-images"
    assert (non_images / "notes.txt").is_file()
    assert (non_images / "2024" / "manual.pdf").is_file()

    result = run_restore(RestoreOptions(dups_folder=non_images), QUIET)
    assert result.manifest_kind == "sweep"
    assert result.files_restored == 2
    assert (src / "notes.txt").is_file()
    assert (src / "2024" / "manual.pdf").is_file()


def test_sweep_junk_restore_reports_deletes_no_op(tmp_path: Path):
    """Junk-delete entries are intentionally one-way; restore should
    count them and exit cleanly without trying to recreate anything."""
    src = _build_sweep_tree(tmp_path)
    run_sweep(SweepOptions(source=src, sweep_junk=True), QUIET)

    log_folder = src.parent / f"{src.name}-sweep-log"
    assert (log_folder / "sweep-manifest.json").is_file()

    result = run_restore(RestoreOptions(dups_folder=log_folder), QUIET)
    assert result.manifest_kind == "sweep"
    assert result.files_restored == 0
    assert result.deleted_entries == 1  # Thumbs.db
    assert result.files_skipped == 0
    assert not result.conflicts
    assert not result.errors

    # Junk file still gone — restore reports but does not attempt recreate.
    assert not (src / "Thumbs.db").exists()


def test_sweep_quarantine_junk_restore_reverses_moves(tmp_path: Path):
    """When --junk is paired with --quarantine-junk the entries are
    action=moved (not deleted), so restore reverses them like any other
    move."""
    src = _build_sweep_tree(tmp_path)
    run_sweep(
        SweepOptions(source=src, sweep_junk=True, quarantine_junk=True),
        QUIET,
    )

    junk_folder = src.parent / f"{src.name}-junk"
    assert (junk_folder / "Thumbs.db").is_file()
    assert not (src / "Thumbs.db").exists()

    result = run_restore(RestoreOptions(dups_folder=junk_folder), QUIET)
    assert result.manifest_kind == "sweep"
    assert result.files_restored == 1
    assert result.deleted_entries == 0
    assert (src / "Thumbs.db").is_file()


def test_sweep_restore_refuses_to_overwrite(tmp_path: Path):
    """If the original location is occupied at restore time, the entry
    is reported as a conflict and skipped — same contract as scan."""
    src = _build_sweep_tree(tmp_path)
    run_sweep(SweepOptions(source=src, sweep_videos=True), QUIET)

    videos_folder = src.parent / f"{src.name} - videos"
    # Put something at the original path before restoring.
    blocker = src / "trip.mov"
    blocker.write_text("not the original")

    result = run_restore(RestoreOptions(dups_folder=videos_folder), QUIET)
    assert result.files_skipped == 1
    assert result.files_restored == 1  # the other video (2024/ski.mp4)
    assert any("trip.mov" in c for c in result.conflicts)
    # Blocker untouched
    assert blocker.read_text() == "not the original"
    # Other video back where it belongs
    assert (src / "2024" / "ski.mp4").is_file()


def test_sweep_restore_handles_missing_quarantined_file(tmp_path: Path):
    """If a quarantined file was manually deleted between sweep and
    restore, the entry is reported as an error but doesn't crash."""
    src = _build_sweep_tree(tmp_path)
    run_sweep(SweepOptions(source=src, sweep_videos=True), QUIET)

    videos_folder = src.parent / f"{src.name} - videos"
    # Delete one of the quarantined files manually.
    (videos_folder / "trip.mov").unlink()

    result = run_restore(RestoreOptions(dups_folder=videos_folder), QUIET)
    assert result.files_restored == 1  # the other one survives
    assert any("missing in sweep folder" in e for e in result.errors)


def test_restore_ambiguous_folder_with_both_manifests_raises(tmp_path: Path):
    """Defensive: a folder containing BOTH manifest types is rejected
    so we never silently pick one and surprise the user."""
    folder = tmp_path / "both"
    folder.mkdir()
    # Minimal valid sweep manifest
    (folder / "sweep-manifest.json").write_text(
        json.dumps({"version": 1, "category": "videos", "mode": "quarantine", "entries": []})
    )
    # Minimal valid scan manifest
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "created_at": "",
                "source_folder": "",
                "dups_folder": "",
                "entries": [],
            }
        )
    )

    with pytest.raises(ValueError, match="both scan and sweep manifests"):
        run_restore(RestoreOptions(dups_folder=folder), QUIET)


def test_sweep_restore_rejects_unsupported_manifest_version(tmp_path: Path):
    """A future-version manifest must fail loudly rather than silently
    skipping entries we don't understand."""
    folder = tmp_path / "future"
    folder.mkdir()
    (folder / "sweep-manifest.json").write_text(
        json.dumps({"version": 99, "category": "videos", "mode": "quarantine", "entries": []})
    )
    with pytest.raises(ValueError, match="unsupported sweep manifest version"):
        run_restore(RestoreOptions(dups_folder=folder), QUIET)
