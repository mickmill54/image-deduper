"""Tests for dedupe.hash_cache."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dedupe.hash_cache import HASH_CACHE_NAME, HashCache


def _write_file(p: Path, content: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def test_open_creates_no_file_until_first_set(tmp_path: Path):
    """Opening the cache must not side-effect the filesystem — the
    file (and its parent dups folder) is created lazily on the first
    write so a no-op scan stays clean."""
    src = tmp_path / "Photos"
    src.mkdir()
    dups = tmp_path / "dups"

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    assert len(cache) == 0
    assert not dups.exists()  # nothing written yet


def test_first_set_creates_file_with_header(tmp_path: Path):
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    cache.set(target, "deadbeef")

    cache_path = dups / HASH_CACHE_NAME
    assert cache_path.is_file()
    lines = cache_path.read_text().strip().splitlines()
    assert len(lines) == 2  # header + one entry
    header = json.loads(lines[0])
    assert header["_header"]["version"] == 1
    assert header["_header"]["source_folder"] == str(src.resolve())
    entry = json.loads(lines[1])
    assert entry["path"] == str(target)
    assert entry["sha256"] == "deadbeef"


def test_get_returns_cached_digest_on_hit(tmp_path: Path):
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    cache.set(target, "deadbeef")
    assert cache.get(target) == "deadbeef"


def test_get_misses_when_mtime_changes(tmp_path: Path):
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    cache.set(target, "deadbeef")

    # Bump mtime by 10 seconds — modeled close enough to a "user
    # touched the file between scans" event.
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    assert cache.get(target) is None


def test_get_misses_when_size_changes(tmp_path: Path):
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    cache.set(target, "deadbeef")

    # Append a byte → size differs from cached.
    with target.open("ab") as fh:
        fh.write(b"!")
    assert cache.get(target) is None


def test_get_misses_for_unknown_path(tmp_path: Path):
    src = tmp_path / "Photos"
    src.mkdir()
    cache = HashCache.open(dups_folder=tmp_path / "dups", source_folder=src)
    assert cache.get(src / "never.bin") is None


def test_reopen_loads_existing_entries(tmp_path: Path):
    """Cache survives across HashCache.open() calls, which is the
    whole point — a Ctrl-C and re-run should reuse hashes."""
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"

    cache1 = HashCache.open(dups_folder=dups, source_folder=src)
    cache1.set(target, "deadbeef")

    cache2 = HashCache.open(dups_folder=dups, source_folder=src)
    assert len(cache2) == 1
    assert cache2.get(target) == "deadbeef"


def test_reopen_with_different_source_discards_cache(tmp_path: Path):
    """A cache from a different source folder is wrong-context and
    must be discarded rather than silently mixing two scans."""
    src1 = tmp_path / "Photos1"
    src1.mkdir()
    src2 = tmp_path / "Photos2"
    src2.mkdir()
    target = src1 / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"

    cache1 = HashCache.open(dups_folder=dups, source_folder=src1)
    cache1.set(target, "deadbeef")
    assert (dups / HASH_CACHE_NAME).is_file()

    cache2 = HashCache.open(dups_folder=dups, source_folder=src2)
    assert len(cache2) == 0
    # Stale cache file removed, ready to be replaced on next set()
    assert not (dups / HASH_CACHE_NAME).exists()


def test_reopen_with_unsupported_version_discards_cache(tmp_path: Path):
    """A cache file with a future version must be discarded — silently
    skipping unparsed entries is worse than starting fresh."""
    src = tmp_path / "Photos"
    src.mkdir()
    dups = tmp_path / "dups"
    dups.mkdir()
    cache_path = dups / HASH_CACHE_NAME
    cache_path.write_text(
        json.dumps({"_header": {"version": 99, "source_folder": str(src.resolve())}}) + "\n"
    )

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    assert len(cache) == 0
    assert not cache_path.exists()


def test_reopen_with_corrupt_header_discards_cache(tmp_path: Path):
    src = tmp_path / "Photos"
    src.mkdir()
    dups = tmp_path / "dups"
    dups.mkdir()
    cache_path = dups / HASH_CACHE_NAME
    cache_path.write_text("this is not valid JSON\n")

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    assert len(cache) == 0


def test_reopen_skips_individual_corrupt_entry_lines(tmp_path: Path):
    """A single bad line shouldn't sink the whole cache — read the
    others and move on. The worst case is one extra fresh hash."""
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"
    dups.mkdir()
    cache_path = dups / HASH_CACHE_NAME

    header = {"_header": {"version": 1, "source_folder": str(src.resolve())}}
    valid_entry = {
        "path": str(target),
        "mtime_ns": target.stat().st_mtime_ns,
        "size": target.stat().st_size,
        "sha256": "deadbeef",
    }
    cache_path.write_text(
        json.dumps(header) + "\n" + "garbage line\n" + json.dumps(valid_entry) + "\n"
    )

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    assert len(cache) == 1
    assert cache.get(target) == "deadbeef"


def test_set_overrides_earlier_entry(tmp_path: Path):
    """Append-only file with later wins — useful when a file is
    re-hashed after modification."""
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    cache.set(target, "first-digest")
    cache.set(target, "second-digest")
    assert cache.get(target) == "second-digest"

    # Reload to confirm persistence honors the same precedence.
    cache2 = HashCache.open(dups_folder=dups, source_folder=src)
    assert cache2.get(target) == "second-digest"


def test_load_read_only(tmp_path: Path):
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"

    writer = HashCache.open(dups_folder=dups, source_folder=src)
    writer.set(target, "deadbeef")

    # Read-only loader should see the same entry but refuse writes.
    reader = HashCache.load(dups / HASH_CACHE_NAME)
    assert reader is not None
    assert reader.get(target) == "deadbeef"
    reader.set(target, "modified")  # silent no-op — no exception
    # Writer's view unchanged
    assert writer.get(target) == "deadbeef"


def test_load_returns_none_for_missing(tmp_path: Path):
    assert HashCache.load(tmp_path / "nope.jsonl") is None


def test_threadsafe_concurrent_set(tmp_path: Path):
    """Many threads writing to the same cache concurrently must not
    corrupt the JSONL. Re-loading should yield exactly the entries we
    wrote (order doesn't matter; latest wins per path)."""
    import threading  # noqa: PLC0415

    src = tmp_path / "Photos"
    src.mkdir()
    files = [src / f"f{i}.bin" for i in range(50)]
    for p in files:
        _write_file(p, b"x")
    dups = tmp_path / "dups"

    cache = HashCache.open(dups_folder=dups, source_folder=src)

    def write_one(p: Path) -> None:
        cache.set(p, f"digest-{p.name}")

    threads = [threading.Thread(target=write_one, args=(p,)) for p in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Reload from disk — every file should be retrievable.
    cache2 = HashCache.open(dups_folder=dups, source_folder=src)
    assert len(cache2) == 50
    for p in files:
        assert cache2.get(p) == f"digest-{p.name}"


def test_open_silently_recreates_after_corrupt_cache_unlink_fails(tmp_path: Path, monkeypatch):
    """If the stale cache can't be removed (permissions, race), the
    cache should still be safe to use — we just start with an empty
    in-memory cache. Defensive but cheap."""
    src = tmp_path / "Photos"
    src.mkdir()
    dups = tmp_path / "dups"
    dups.mkdir()
    cache_path = dups / HASH_CACHE_NAME
    cache_path.write_text("not json\n")

    real_unlink = Path.unlink

    def boom(self, *a, **kw):
        if self == cache_path:
            raise OSError("simulated permission denied")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", boom)
    cache = HashCache.open(dups_folder=dups, source_folder=src)
    assert len(cache) == 0


def test_cache_speedup_on_resume_via_run_scan(tmp_path: Path):
    """End-to-end: run_scan twice; the second run should report cache
    hits for every file. We assert via the cache file's contents
    rather than instrumenting hash_file directly so the test stays
    decoupled from internal call sites."""
    from dedupe.scan import ScanOptions, run_scan  # noqa: PLC0415
    from dedupe.ui import UI, UIConfig  # noqa: PLC0415

    src = tmp_path / "Photos"
    src.mkdir()
    # Three unique files so first run finds no dups and just hashes.
    _write_file(src / "a.bin", b"AAAA")
    _write_file(src / "b.bin", b"BBBB")
    _write_file(src / "c.bin", b"CCCC")
    dups = tmp_path / "dups"

    quiet = UI(UIConfig(quiet=True))
    # We're using .bin as the extension to avoid Pillow paths; run_scan
    # filters by IMAGE_EXTENSIONS, so use real image extensions.

    # Switch to .jpg so iter_image_files finds them.
    for p in list(src.iterdir()):
        p.rename(p.with_suffix(".jpg"))

    run_scan(ScanOptions(source=src, dups_folder=dups), quiet)

    cache_path = dups / HASH_CACHE_NAME
    assert cache_path.is_file()
    # Cache has 3 entries from the first run.
    cache = HashCache.load(cache_path)
    assert cache is not None
    assert len(cache) == 3

    # Re-run; cache file still has 3 entries (no fresh writes).
    before_size = cache_path.stat().st_size
    run_scan(ScanOptions(source=src, dups_folder=dups), quiet)
    after_size = cache_path.stat().st_size
    # On re-run all files should hit the cache → no append-writes.
    assert after_size == before_size, "Cache file should not grow on a fully-cached re-run"


@pytest.mark.parametrize("missing_field", ["path", "mtime_ns", "size", "sha256"])
def test_entry_with_missing_field_is_ignored(tmp_path: Path, missing_field: str):
    src = tmp_path / "Photos"
    src.mkdir()
    target = src / "a.bin"
    _write_file(target, b"hello")
    dups = tmp_path / "dups"
    dups.mkdir()
    cache_path = dups / HASH_CACHE_NAME

    header = {"_header": {"version": 1, "source_folder": str(src.resolve())}}
    full_entry = {
        "path": str(target),
        "mtime_ns": target.stat().st_mtime_ns,
        "size": target.stat().st_size,
        "sha256": "deadbeef",
    }
    broken_entry = {k: v for k, v in full_entry.items() if k != missing_field}
    cache_path.write_text(json.dumps(header) + "\n" + json.dumps(broken_entry) + "\n")

    cache = HashCache.open(dups_folder=dups, source_folder=src)
    assert len(cache) == 0  # the broken entry was skipped
