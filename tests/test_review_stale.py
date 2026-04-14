from __future__ import annotations

import datetime as _dt
import hashlib
import io
from pathlib import Path

import pytest

import registry
import review_stale as rs


SID_1 = "11110000-1111-1111-1111-111111111111"
SID_2 = "22220000-2222-2222-2222-222222222222"
SID_3 = "33330000-3333-3333-3333-333333333333"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_stale(sid: str, priority: str = "medium") -> None:
    aged = (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = registry.new_record(sid, priority=priority, title=f"t-{priority}")
    r["last_activity_at"] = aged
    r["status"] = "in_progress"
    registry.write(r)


def _run(input_text: str):
    inp = io.StringIO(input_text)
    out = io.StringIO()
    rc = rs.run(inp=inp, out=out)
    return rc, out.getvalue()


def test_review_stale_keep_is_byte_identical():
    _seed_stale(SID_1)
    h = _sha(registry.record_path(SID_1))
    rc, _ = _run("keep\n")
    assert rc == 0
    assert _sha(registry.record_path(SID_1)) == h


def test_review_stale_skip_is_byte_identical():
    _seed_stale(SID_1)
    h = _sha(registry.record_path(SID_1))
    rc, _ = _run("skip\n")
    assert rc == 0
    assert _sha(registry.record_path(SID_1)) == h


def test_review_stale_done_flips_status():
    _seed_stale(SID_1)
    rc, _ = _run("done\n")
    assert rc == 0
    r = registry.read(SID_1)
    assert r["status"] == "done"
    assert r["auto_detected"] is False


def test_review_stale_archive_sets_archived_at():
    _seed_stale(SID_1)
    rc, _ = _run("archive\n")
    assert rc == 0
    r = registry.read(SID_1)
    assert r["archived"] is True
    assert r["archived_at"]


def test_review_stale_empty_exits_0_with_message():
    # No stale records.
    rc, out = _run("")
    assert rc == 0
    assert "no stale sessions" in out


def test_review_stale_non_interactive_treats_remaining_as_skip():
    _seed_stale(SID_1)
    _seed_stale(SID_2)
    h1 = _sha(registry.record_path(SID_1))
    h2 = _sha(registry.record_path(SID_2))
    # Only one input line — second session sees EOF.
    rc, _ = _run("skip\n")
    assert rc == 0
    assert _sha(registry.record_path(SID_1)) == h1
    assert _sha(registry.record_path(SID_2)) == h2


def test_review_stale_unrecognized_input_reprompts_then_skips():
    _seed_stale(SID_1)
    h = _sha(registry.record_path(SID_1))
    # Three bad answers → defensive skip.
    rc, out = _run("zzz\nbleh\nnope\n")
    assert rc == 0
    assert out.count("please answer") >= 2
    assert _sha(registry.record_path(SID_1)) == h


def test_review_stale_presents_three_sessions_in_priority_order():
    _seed_stale(SID_1, priority="high")
    _seed_stale(SID_2, priority="medium")
    _seed_stale(SID_3, priority="low")
    h1 = _sha(registry.record_path(SID_1))
    h2 = _sha(registry.record_path(SID_2))
    h3 = _sha(registry.record_path(SID_3))
    rc, out = _run("skip\nskip\nskip\n")
    assert rc == 0
    idx_1 = out.find(SID_1[:8])
    idx_2 = out.find(SID_2[:8])
    idx_3 = out.find(SID_3[:8])
    assert idx_1 != -1 and idx_2 != -1 and idx_3 != -1
    assert idx_1 < idx_2 < idx_3
    # All byte-identical.
    assert _sha(registry.record_path(SID_1)) == h1
    assert _sha(registry.record_path(SID_2)) == h2
    assert _sha(registry.record_path(SID_3)) == h3
