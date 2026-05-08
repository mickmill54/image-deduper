"""Tests for dedupe.sweep."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dedupe.sweep import (
    ACTION_DELETED,
    ACTION_MOVED,
    JUNK_FILES,
    VIDEO_EXTENSIONS,
    SweepOptions,
    _videos_dest_for,
    is_image_file,
    is_junk_file,
    is_video_file,
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


# --- classification helpers -------------------------------------------------


def test_video_extensions_constant_contents():
    # The full documented allowlist (19 extensions). Update this test in
    # lockstep with VIDEO_EXTENSIONS to make additions explicit in code review.
    expected = {
        ".mov",
        ".mp4",
        ".m4v",  # Apple / modern phones
        ".avi",
        ".mkv",
        ".wmv",
        ".asf",  # Older Windows / generic
        ".flv",
        ".f4v",  # Flash (legacy web)
        ".webm",  # Open web video
        ".mpg",
        ".mpeg",  # MPEG program streams
        ".3gp",
        ".3g2",  # Mobile (3GPP / 3GPP2)
        ".mts",
        ".m2ts",  # AVCHD camcorders
        ".vob",  # DVD video
        ".ogv",  # Ogg video
        ".divx",  # DivX-encoded AVI variant
        ".lrv",  # GoPro preview file
    }
    assert expected == VIDEO_EXTENSIONS
    # Lowercase by design — classifier normalizes via path.suffix.lower().
    assert ".MP4" not in VIDEO_EXTENSIONS


def test_video_extensions_deliberately_excluded():
    """Document the formats we explicitly chose NOT to include, so a
    drive-by addition has to confront the rationale comment in sweep.py."""
    # MPEG transport stream — conflicts with TypeScript source code.
    assert ".ts" not in VIDEO_EXTENSIONS
    # RealMedia — effectively extinct.
    assert ".rm" not in VIDEO_EXTENSIONS
    assert ".rmvb" not in VIDEO_EXTENSIONS
    # Broadcast / professional — niche.
    assert ".mxf" not in VIDEO_EXTENSIONS
    # Raw codec streams (without containers) — rare in consumer pipelines.
    assert ".hevc" not in VIDEO_EXTENSIONS
    assert ".h264" not in VIDEO_EXTENSIONS


def test_is_video_file(tmp_path: Path):
    assert is_video_file(tmp_path / "movie.mov")
    assert is_video_file(tmp_path / "movie.MP4")  # case-insensitive
    assert not is_video_file(tmp_path / "photo.jpg")


def test_is_video_file_covers_all_documented_extensions(tmp_path: Path):
    """Every extension in the documented allowlist classifies as a video.
    Catches the case where someone adds an extension to VIDEO_EXTENSIONS
    but breaks `is_video_file` (it's a one-line check, but tests are cheap)."""
    for ext in VIDEO_EXTENSIONS:
        assert is_video_file(tmp_path / f"sample{ext}"), f"{ext} should classify as video"
        # Case-insensitivity check (suffix.lower() in the classifier)
        assert is_video_file(
            tmp_path / f"sample{ext.upper()}"
        ), f"{ext.upper()} should classify as video too"


def test_is_video_file_rejects_excluded_extensions(tmp_path: Path):
    """Files with deliberately-excluded extensions must NOT classify as videos
    (otherwise --videos --non-images would route them to the wrong category)."""
    for ext in (".ts", ".rm", ".rmvb", ".mxf", ".hevc", ".h264"):
        assert not is_video_file(
            tmp_path / f"sample{ext}"
        ), f"{ext} is deliberately excluded; must not classify as video"


def test_is_image_file(tmp_path: Path):
    assert is_image_file(tmp_path / "photo.jpg")
    assert is_image_file(tmp_path / "photo.HEIC")
    assert not is_image_file(tmp_path / "movie.mov")
    assert not is_image_file(tmp_path / "notes.txt")


# --- non-images mode --------------------------------------------------------


@pytest.fixture
def mixed_tree(tmp_path: Path) -> Path:
    """Folder with images + non-images + videos + junk + a hidden image.

    Used for the combined-mode and per-mode tests below.

    Layout:
        root/
          photo.jpg            (preserved by sweep — image)
          notes.txt            (non-image; user content)
          manual.pdf           (non-image)
          archive.zip          (non-image)
          birthday.mov         (video)
          ski.mp4              (video)
          Thumbs.db            (junk)
          .DS_Store            (junk)
          sub/
            other_photo.jpg    (preserved)
            song.mp3           (non-image)
            trip.MOV           (video; case-insensitive ext check)
            Thumbs.db          (junk; same name as top — mirror disambiguates)
    """
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "photo.jpg").write_bytes(b"\xff\xd8")
    (root / "notes.txt").write_text("notes")
    (root / "manual.pdf").write_bytes(b"%PDF-")
    (root / "archive.zip").write_bytes(b"PK")
    (root / "birthday.mov").write_bytes(b"video1")
    (root / "ski.mp4").write_bytes(b"video2")
    (root / "Thumbs.db").write_text("cache")
    (root / ".DS_Store").write_text("meta")
    (root / "sub").mkdir()
    (root / "sub" / "other_photo.jpg").write_bytes(b"\xff\xd8\x02")
    (root / "sub" / "song.mp3").write_bytes(b"ID3")
    (root / "sub" / "trip.MOV").write_bytes(b"video3")
    (root / "sub" / "Thumbs.db").write_text("more cache")
    return root


def test_non_images_moves_to_quarantine_mirrors_layout(mixed_tree: Path, tmp_path: Path):
    dest = tmp_path / "out-non-images"
    result = run_sweep(
        SweepOptions(
            source=mixed_tree,
            sweep_non_images=True,
            non_images_folder=dest,
        ),
        QUIET,
    )
    # 4 non-image files: notes.txt, manual.pdf, archive.zip, sub/song.mp3
    # (videos and junk are NOT non-images — they have their own categories
    # and are skipped when only --non-images is set).
    assert result.non_images_swept == 4
    assert result.files_swept == 4
    assert result.junk_swept == 0
    assert result.videos_swept == 0

    # Source: images preserved, junk preserved, videos preserved
    assert (mixed_tree / "photo.jpg").exists()
    assert (mixed_tree / "sub" / "other_photo.jpg").exists()
    assert (mixed_tree / "Thumbs.db").exists()
    assert (mixed_tree / "birthday.mov").exists()

    # Destination: non-images moved with mirrored layout
    assert (dest / "notes.txt").is_file()
    assert (dest / "manual.pdf").is_file()
    assert (dest / "archive.zip").is_file()
    assert (dest / "sub" / "song.mp3").is_file()
    assert (dest / "sweep-manifest.json").is_file()

    # Manifest entries all action=moved
    data = json.loads((dest / "sweep-manifest.json").read_text())
    assert data["category"] == "non-images"
    assert data["mode"] == "quarantine"
    assert all(e["action"] == ACTION_MOVED for e in data["entries"])


def test_non_images_default_destination(mixed_tree: Path):
    result = run_sweep(SweepOptions(source=mixed_tree, sweep_non_images=True), QUIET)
    assert result.non_images_swept == 4
    expected = mixed_tree.parent / f"{mixed_tree.name}-non-images"
    assert expected.is_dir()
    assert (expected / "sweep-manifest.json").is_file()


def test_non_images_dry_run_makes_no_changes(mixed_tree: Path, tmp_path: Path):
    dest = tmp_path / "out-non-images"
    result = run_sweep(
        SweepOptions(
            source=mixed_tree,
            sweep_non_images=True,
            non_images_folder=dest,
            dry_run=True,
        ),
        QUIET,
    )
    assert result.non_images_swept == 4
    # Source untouched
    assert (mixed_tree / "notes.txt").exists()
    assert (mixed_tree / "sub" / "song.mp3").exists()
    # No destination created
    assert not dest.exists()


# --- videos mode ------------------------------------------------------------


def test_videos_moves_to_default_dash_videos_folder(mixed_tree: Path):
    """Default destination is `<source> - videos` and every mirrored
    subdirectory inside gains a ` - videos` suffix so paths remain
    self-documenting if the folder is moved out of context."""
    result = run_sweep(SweepOptions(source=mixed_tree, sweep_videos=True), QUIET)
    # 3 videos: birthday.mov, ski.mp4, sub/trip.MOV (case-insensitive ext)
    assert result.videos_swept == 3
    assert result.files_swept == 3

    expected_destination = mixed_tree.parent / f"{mixed_tree.name} - videos"
    assert expected_destination.is_dir()
    # Source-root videos go directly under the wrapper (no extra subfolder)
    assert (expected_destination / "birthday.mov").is_file()
    assert (expected_destination / "ski.mp4").is_file()
    # `sub/` becomes `sub - videos/` inside the wrapper
    assert (expected_destination / "sub - videos" / "trip.MOV").is_file()
    # The un-suffixed `sub/` path must NOT be created
    assert not (expected_destination / "sub").exists()
    assert (expected_destination / "sweep-manifest.json").is_file()

    # Source: videos gone, images preserved
    assert not (mixed_tree / "birthday.mov").exists()
    assert (mixed_tree / "photo.jpg").exists()
    assert (mixed_tree / "sub" / "other_photo.jpg").exists()


def test_videos_dest_for_helper():
    """Unit test the path-translation helper so the contract is pinned."""
    # Source-root file: no parents to suffix.
    assert _videos_dest_for(Path("trip.mov")) == Path("trip.mov")
    # Single subdir gains the suffix.
    assert _videos_dest_for(Path("2008 - iPhone/movie.mov")) == Path(
        "2008 - iPhone - videos/movie.mov"
    )
    # Every parent component gains the suffix; basename stays put.
    assert _videos_dest_for(Path("2009 - iPhone/archive/old.mov")) == Path(
        "2009 - iPhone - videos/archive - videos/old.mov"
    )
    # Idempotent: a parent that already ends in " - videos" is left alone.
    assert _videos_dest_for(Path("trip - videos/clip.mov")) == Path("trip - videos/clip.mov")


def test_videos_custom_destination(mixed_tree: Path, tmp_path: Path):
    dest = tmp_path / "video-archive"
    result = run_sweep(
        SweepOptions(source=mixed_tree, sweep_videos=True, videos_folder=dest),
        QUIET,
    )
    assert result.videos_swept == 3
    assert (dest / "birthday.mov").is_file()


def test_videos_does_not_touch_non_videos(mixed_tree: Path):
    run_sweep(SweepOptions(source=mixed_tree, sweep_videos=True), QUIET)
    # Non-image, non-video files preserved
    assert (mixed_tree / "notes.txt").exists()
    assert (mixed_tree / "manual.pdf").exists()
    assert (mixed_tree / "Thumbs.db").exists()


# --- combined modes ---------------------------------------------------------


def test_all_three_modes_combined(mixed_tree: Path, tmp_path: Path):
    """Single invocation handles junk + non-images + videos. Each
    category writes to its own destination + manifest."""
    junk_log = tmp_path / "junk-log"
    non_images = tmp_path / "non-images"
    videos = tmp_path / "videos"

    result = run_sweep(
        SweepOptions(
            source=mixed_tree,
            sweep_junk=True,
            log_folder=junk_log,
            sweep_non_images=True,
            non_images_folder=non_images,
            sweep_videos=True,
            videos_folder=videos,
        ),
        QUIET,
    )

    # 3 junk + 4 non-images + 3 videos = 10 total
    assert result.junk_swept == 3  # Thumbs.db (×2), .DS_Store
    assert result.non_images_swept == 4
    assert result.videos_swept == 3
    assert result.files_swept == 10

    # Junk: deleted (default mode) — not present anywhere on disk
    assert not (mixed_tree / "Thumbs.db").exists()
    assert not (mixed_tree / "sub" / "Thumbs.db").exists()
    assert not (mixed_tree / ".DS_Store").exists()
    # Junk audit log present
    assert (junk_log / "sweep-manifest.json").is_file()
    junk_data = json.loads((junk_log / "sweep-manifest.json").read_text())
    assert junk_data["category"] == "junk"
    assert junk_data["mode"] == "delete"
    assert all(e["action"] == ACTION_DELETED for e in junk_data["entries"])

    # Non-images: moved to their folder
    assert (non_images / "notes.txt").is_file()
    assert (non_images / "sub" / "song.mp3").is_file()

    # Videos: moved to their folder; sub/ gains the ` - videos` suffix
    assert (videos / "birthday.mov").is_file()
    assert (videos / "sub - videos" / "trip.MOV").is_file()

    # Images: preserved
    assert (mixed_tree / "photo.jpg").exists()
    assert (mixed_tree / "sub" / "other_photo.jpg").exists()


def test_combined_dry_run_makes_no_changes(mixed_tree: Path, tmp_path: Path):
    junk_log = tmp_path / "junk-log"
    non_images = tmp_path / "non-images"
    videos = tmp_path / "videos"

    result = run_sweep(
        SweepOptions(
            source=mixed_tree,
            sweep_junk=True,
            log_folder=junk_log,
            sweep_non_images=True,
            non_images_folder=non_images,
            sweep_videos=True,
            videos_folder=videos,
            dry_run=True,
        ),
        QUIET,
    )
    # All counted but nothing actually moved/deleted.
    assert result.files_swept == 10
    # All originals still in place
    assert (mixed_tree / "Thumbs.db").exists()
    assert (mixed_tree / "notes.txt").exists()
    assert (mixed_tree / "birthday.mov").exists()
    # No destinations created
    assert not junk_log.exists()
    assert not non_images.exists()
    assert not videos.exists()


def test_combined_excludes_image_files(mixed_tree: Path):
    """Image files are NEVER touched, regardless of which modes are on."""
    run_sweep(
        SweepOptions(
            source=mixed_tree,
            sweep_junk=True,
            sweep_non_images=True,
            sweep_videos=True,
        ),
        QUIET,
    )
    # All JPGs survived
    assert (mixed_tree / "photo.jpg").exists()
    assert (mixed_tree / "sub" / "other_photo.jpg").exists()


def test_exclude_pattern_applies_across_modes(mixed_tree: Path):
    """`--exclude` filters before the category dispatch — a sub/* exclude
    should preserve everything inside sub/ regardless of category."""
    result = run_sweep(
        SweepOptions(
            source=mixed_tree,
            sweep_junk=True,
            sweep_non_images=True,
            sweep_videos=True,
            exclude_patterns=("sub/*",),
        ),
        QUIET,
    )
    # Without exclude: 3 junk + 4 non-images + 3 videos = 10
    # With sub/* excluded: drop sub/Thumbs.db + sub/song.mp3 + sub/trip.MOV = 3
    # So 10 - 3 = 7
    assert result.files_swept == 7
    # sub/* contents preserved entirely
    assert (mixed_tree / "sub" / "Thumbs.db").exists()
    assert (mixed_tree / "sub" / "song.mp3").exists()
    assert (mixed_tree / "sub" / "trip.MOV").exists()
    assert (mixed_tree / "sub" / "other_photo.jpg").exists()


def test_videos_refuses_to_overwrite(mixed_tree: Path, tmp_path: Path):
    dest = tmp_path / "videos"
    dest.mkdir()
    # Pre-existing file at one of the destinations.
    (dest / "birthday.mov").write_bytes(b"existing")

    result = run_sweep(
        SweepOptions(source=mixed_tree, sweep_videos=True, videos_folder=dest),
        QUIET,
    )
    # 3 videos total; 1 conflict; 2 succeed.
    assert result.videos_swept == 2
    assert any("refusing to overwrite" in e for e in result.errors)
    # Pre-existing file untouched
    assert (dest / "birthday.mov").read_bytes() == b"existing"
    # Source file that couldn't be moved is still in place
    assert (mixed_tree / "birthday.mov").exists()
