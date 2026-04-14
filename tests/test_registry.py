from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

import registry


SID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def test_create_read_roundtrip():
    rec = registry.new_record(SID_A, title="hello", priority="high")
    registry.write(rec)
    got = registry.read(SID_A)
    assert got is not None
    assert got["session_id"] == SID_A
    assert got["title"] == "hello"
    assert got["priority"] == "high"


def test_update_flips_auto_detected():
    registry.write(registry.new_record(SID_A))
    assert registry.read(SID_A)["auto_detected"] is True
    registry.update(SID_A, title="User")
    assert registry.read(SID_A)["auto_detected"] is False
    assert registry.read(SID_A)["title"] == "User"


def test_atomic_write_no_partial(monkeypatch):
    """If ``os.replace`` fails, no partial file is left at the destination."""
    registry.write(registry.new_record(SID_A, title="orig"))

    real_replace = os.replace
    call = {"n": 0}

    def boom(src, dst):
        call["n"] += 1
        # Raise on the FIRST replace call (the write under test),
        # passing through afterward to avoid breaking teardown.
        if call["n"] == 1:
            # Remove the tempfile so it can't be mistaken for a partial.
            try:
                os.unlink(src)
            except FileNotFoundError:
                pass
            raise OSError("simulated replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(registry.os, "replace", boom)

    with pytest.raises(OSError):
        registry.write(registry.new_record(SID_A, title="new"))

    # Destination file still contains the original content.
    got = registry.read(SID_A)
    assert got["title"] == "orig"
    # And no stray .tmp files remain.
    for p in registry.registry_dir().iterdir():
        assert not p.name.endswith(".tmp"), p


def test_corrupt_file_isolated():
    # One good record, one bad JSON file, one good record.
    registry.write(registry.new_record(SID_A, title="good-a"))
    registry.write(registry.new_record(SID_B, title="good-b"))
    bad = registry.registry_dir() / "badfile.json"
    bad.write_text("{not valid json", encoding="utf-8")

    records = list(registry.iter_records())
    titles = sorted(r["title"] for r in records)
    assert titles == ["good-a", "good-b"]

    # Bad file is isolated.
    remaining_bad = list(registry.registry_dir().glob("badfile.json.corrupt-*"))
    assert len(remaining_bad) >= 1
    # And the original plain badfile.json is gone.
    assert not bad.exists()
    # Bytes are preserved byte-for-byte.
    assert remaining_bad[0].read_text(encoding="utf-8") == "{not valid json"


def test_concurrent_writes():
    """Two threads writing distinct records both succeed."""
    def worker(sid: str, title: str):
        registry.write(registry.new_record(sid, title=title))

    t1 = threading.Thread(target=worker, args=(SID_A, "A"))
    t2 = threading.Thread(target=worker, args=(SID_B, "B"))
    t1.start(); t2.start(); t1.join(); t2.join()

    a = registry.read(SID_A)
    b = registry.read(SID_B)
    assert a["title"] == "A"
    assert b["title"] == "B"


def test_sort_priority_and_recency():
    # Build records with known activity timestamps (lexicographic ordering
    # matches chronological for ISO-Z timestamps).
    def r(sid: str, pri: str, ts: str, title: str):
        rec = registry.new_record(sid, priority=pri, title=title)
        rec["last_activity_at"] = ts
        registry.write(rec)

    r("00000000-0000-0000-0000-000000000001", "medium", "2025-01-01T00:00:10Z", "M-recent")
    r("00000000-0000-0000-0000-000000000002", "high",   "2025-01-01T00:00:05Z", "H-old")
    r("00000000-0000-0000-0000-000000000003", "high",   "2025-01-01T00:00:55Z", "H-recent")
    r("00000000-0000-0000-0000-000000000004", "low",    "2025-01-01T00:00:59Z", "L-newest")

    ordered = registry.sorted_records()
    titles = [x["title"] for x in ordered]
    assert titles == ["H-recent", "H-old", "M-recent", "L-newest"]
