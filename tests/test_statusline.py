from __future__ import annotations

import datetime as _dt
import io
import time

import pytest

import registry
import statusline as sl


def _seed(sid: str, *, hours_old: float = 0, status="in_progress", archived=False):
    r = registry.new_record(sid, status=status)
    r["archived"] = archived
    if hours_old:
        r["last_activity_at"] = (
            _dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(hours=hours_old)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    registry.write(r)


def test_statusline_empty_registry_prints_empty_line(capsys):
    rc = sl.run()
    assert rc == 0
    out = capsys.readouterr().out
    assert out == "\n"


def test_statusline_empty_registry_omits_arrow_and_tasks_nudge(capsys):
    rc = sl.run()
    out = capsys.readouterr().out
    assert "\u2192" not in out   # no →
    assert "/tasks" not in out


def test_statusline_pending_only_shape(capsys):
    _seed("11111111-2222-3333-4444-555555555555")
    sl.run()
    out = capsys.readouterr().out.rstrip("\n")
    assert out == "\U0001f4cb 1 pending  \u2192  /tasks"


def test_statusline_pending_and_stale_shape(capsys):
    _seed("11111111-2222-3333-4444-555555555555", hours_old=5)
    sl.run()
    out = capsys.readouterr().out.rstrip("\n")
    assert out == "\U0001f4cb 1 pending \u00b7 1 stale  \u2192  /tasks"


def test_statusline_omits_stale_segment_when_zero(capsys):
    _seed("11111111-2222-3333-4444-555555555555", hours_old=0)
    sl.run()
    out = capsys.readouterr().out.rstrip("\n")
    assert "stale" not in out


def test_statusline_200_records_under_150ms():
    for i in range(200):
        sid = f"{i:08x}-0000-0000-0000-{i:012x}"
        _seed(sid)
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        sl.run()
        dt = time.perf_counter() - t0
        best = min(best, dt)
    # Give CI some slack but still meaningful.
    assert best < 0.15, f"statusline too slow: {best:.3f}s"


def test_statusline_does_not_invoke_subprocess(monkeypatch):
    import subprocess

    def boom(*a, **kw):
        raise RuntimeError("statusline must NOT call subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    _seed("11111111-2222-3333-4444-555555555555")
    # Must succeed.
    rc = sl.run()
    assert rc == 0
