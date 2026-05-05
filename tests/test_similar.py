"""Tests for dedupe.similar."""

from __future__ import annotations

from pathlib import Path

from dedupe.similar import SimilarOptions, run_find_similar
from dedupe.ui import UI, UIConfig

QUIET = UI(UIConfig(quiet=True))


def test_find_similar_groups_visually_close_images(similar_tree: Path, tmp_path: Path):
    report = tmp_path / "report.html"
    opts = SimilarOptions(source=similar_tree, threshold=10, report_path=report)
    result = run_find_similar(opts, QUIET)

    assert result.files_scanned == 4
    # The three burst images should be in one group; the unrelated image excluded.
    assert len(result.groups) == 1
    members = {p.name for p in result.groups[0].members}
    assert {"burst_1.jpg", "burst_2.jpg", "burst_3.jpg"}.issubset(members)
    assert "unrelated.jpg" not in members


def test_find_similar_writes_self_contained_html(similar_tree: Path, tmp_path: Path):
    report = tmp_path / "out" / "report.html"
    opts = SimilarOptions(source=similar_tree, threshold=10, report_path=report)
    run_find_similar(opts, QUIET)

    assert report.is_file()
    content = report.read_text(encoding="utf-8")
    # Self-contained: thumbnails are embedded as data URIs
    assert "data:image/jpeg;base64," in content
    # Report mentions "Group" headings
    assert "Group 1" in content
    # Source folder is referenced in the report
    assert str(similar_tree) in content


def test_find_similar_does_not_move_files(similar_tree: Path, tmp_path: Path):
    before = sorted(p.name for p in similar_tree.iterdir())
    opts = SimilarOptions(
        source=similar_tree,
        threshold=10,
        report_path=tmp_path / "report.html",
    )
    run_find_similar(opts, QUIET)
    after = sorted(p.name for p in similar_tree.iterdir())
    assert before == after


def test_find_similar_excludes_unrelated_image(similar_tree: Path, tmp_path: Path):
    """Even at threshold 0, the unrelated image must not be grouped with the bursts.

    The burst trio is engineered to be phash-identical (distance 0) but
    byte-different. The unrelated image is phash-distant. Whatever threshold
    is in play, those two clusters must not merge.
    """
    opts = SimilarOptions(
        source=similar_tree,
        threshold=0,
        report_path=tmp_path / "report.html",
    )
    result = run_find_similar(opts, QUIET)
    # Exactly one group, containing the burst trio only.
    assert len(result.groups) == 1
    members = {p.name for p in result.groups[0].members}
    assert "unrelated.jpg" not in members
    assert {"burst_1.jpg", "burst_2.jpg", "burst_3.jpg"}.issubset(members)


def test_find_similar_empty_folder(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    opts = SimilarOptions(source=empty, threshold=5, report_path=tmp_path / "report.html")
    result = run_find_similar(opts, QUIET)
    assert result.files_scanned == 0
    assert result.groups == []
