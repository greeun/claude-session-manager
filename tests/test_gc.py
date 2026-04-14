from __future__ import annotations

import datetime as _dt
import os

import pytest

import csm_gc as gc
import registry


SID_OLD = "11112222-3333-4444-5555-666677778888"
SID_NEW = "aabbccdd-eeff-0011-2233-445566778899"
SID_LIVE = "00001111-2222-3333-4444-555566667777"


def _seed(sid: str, *, archived: bool, archived_at: str | None,
          status: str = "in_progress") -> None:
    r = registry.new_record(sid)
    r["archived"] = archived
    r["archived_at"] = archived_at
    r["status"] = status
    registry.write(r)


def _iso(days_ago: float) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    return (now - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_gc_deletes_only_old_archived(capsys):
    _seed(SID_OLD, archived=True, archived_at=_iso(8))
    _seed(SID_NEW, archived=True, archived_at=_iso(2))
    _seed(SID_LIVE, archived=False, archived_at=None)
    rc = gc.run()
    assert rc == 0
    assert not registry.record_path(SID_OLD).exists()
    assert registry.record_path(SID_NEW).exists()
    assert registry.record_path(SID_LIVE).exists()
    out = capsys.readouterr().out.strip()
    assert out == (
        "csm gc: deleted 1 record(s); kept 1 archived record(s) "
        "still within the 7-day window"
    )


def test_gc_never_deletes_non_archived(capsys):
    _seed(SID_LIVE, archived=False, archived_at=None)
    gc.run()
    assert registry.record_path(SID_LIVE).exists()
    out = capsys.readouterr().out
    assert "deleted 0" in out


def test_gc_uses_archived_at_not_mtime(capsys):
    # archived_at says "old"; file mtime is brand new.
    _seed(SID_OLD, archived=True, archived_at=_iso(10))
    p = registry.record_path(SID_OLD)
    os.utime(p, None)  # touch to now
    gc.run()
    assert not p.exists(), "gc must use archived_at, not mtime"


def test_gc_tolerates_unparseable_archived_at(capsys):
    _seed(SID_OLD, archived=True, archived_at="not-a-timestamp")
    rc = gc.run()
    assert rc == 0
    assert registry.record_path(SID_OLD).exists()
    # Warning logged.
    log = gc._scanner_log_path()
    assert log.exists()
    assert "unparseable archived_at" in log.read_text(encoding="utf-8")


def test_gc_partial_failure_continues_and_exits_1(monkeypatch, capsys):
    _seed(SID_OLD, archived=True, archived_at=_iso(8))
    _seed(SID_NEW, archived=True, archived_at=_iso(9))

    real_unlink = os.unlink

    def selective_unlink(path):
        if str(path).endswith(f"{SID_OLD}.json"):
            raise OSError("simulated permission denied")
        return real_unlink(path)

    monkeypatch.setattr(gc.os, "unlink", selective_unlink)
    rc = gc.run()
    assert rc == 1   # partial failure
    assert registry.record_path(SID_OLD).exists()
    assert not registry.record_path(SID_NEW).exists()  # the other one still deleted
