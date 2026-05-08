"""Tests for the comma-list flag flattener used by --source-ext and --exclude."""

from __future__ import annotations

from dedupe.cli.parser import flatten_list_arg as _flatten_list_arg


def test_none_returns_empty():
    assert _flatten_list_arg(None) == []


def test_empty_list_returns_empty():
    assert _flatten_list_arg([]) == []


def test_repeated_form():
    assert _flatten_list_arg(["png", "bmp", "gif"]) == ["png", "bmp", "gif"]


def test_comma_separated_form():
    assert _flatten_list_arg(["png,bmp,gif"]) == ["png", "bmp", "gif"]


def test_mixed_form():
    assert _flatten_list_arg(["png,bmp", "gif", "tiff,webp"]) == [
        "png",
        "bmp",
        "gif",
        "tiff",
        "webp",
    ]


def test_whitespace_trimmed():
    assert _flatten_list_arg([" png , bmp ", "  gif  "]) == ["png", "bmp", "gif"]


def test_empty_tokens_dropped():
    # Trailing comma, double commas, etc. should not produce empty entries.
    assert _flatten_list_arg(["png,,bmp,"]) == ["png", "bmp"]


def test_lowercase_normalization():
    assert _flatten_list_arg(["PNG", "BmP,GIF"], lowercase=True) == ["png", "bmp", "gif"]


def test_ensure_dot_normalization():
    assert _flatten_list_arg(["png", ".bmp", "gif"], ensure_dot=True) == [
        ".png",
        ".bmp",
        ".gif",
    ]


def test_combined_lowercase_and_ensure_dot():
    assert _flatten_list_arg(["PNG,.BMP", "Gif"], lowercase=True, ensure_dot=True) == [
        ".png",
        ".bmp",
        ".gif",
    ]
