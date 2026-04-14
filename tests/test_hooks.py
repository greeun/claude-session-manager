from __future__ import annotations

import datetime as _dt
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

import hooks as hook_mod
import registry


SID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _feed_stdin(monkeypatch, text: str) -> None:
    """Replace sys.stdin with an in-memory stream (non-TTY)."""
    stream = io.StringIO(text)
    # isatty() on StringIO returns False, which is what we want.
    monkeypatch.setattr(sys, "stdin", stream)


def _clear_env(monkeypatch) -> None:
    for k in ("CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR", "PWD", "TERM_PROGRAM"):
        monkeypatch.delenv(k, raising=False)


def test_session_start_hook_creates_record(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_SESSION_ID", SID_A)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/tmp/xyz")
    _feed_stdin(monkeypatch, "")
    rc = hook_mod.session_start()
    assert rc == 0
    rec = registry.read(SID_A)
    assert rec is not None
    assert rec["cwd"] == "/tmp/xyz"


def test_session_start_hook_exits_zero_on_missing_env(monkeypatch):
    _clear_env(monkeypatch)
    _feed_stdin(monkeypatch, "")
    rc = hook_mod.session_start()
    assert rc == 0
    log = registry.registry_dir() / ".hook-errors.log"
    assert log.exists()
    txt = log.read_text(encoding="utf-8")
    assert txt.strip(), "log must be non-empty"
    # Fresh timestamped entry within last 60s.
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", txt)
    assert m is not None
    t = _dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    # UTC timestamp vs. local clock tolerance: compare to wall clock UTC.
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    assert abs((now - t).total_seconds()) < 120
    assert "session_id" in txt or "missing" in txt.lower()


def test_activity_hook_touches_last_activity_at(monkeypatch):
    _clear_env(monkeypatch)
    # Seed a record.
    registry.write(registry.new_record(SID_A))
    old = registry.read(SID_A)["last_activity_at"]
    # Ensure at least 1 second passes for a distinguishable timestamp.
    time.sleep(1.05)
    monkeypatch.setenv("CLAUDE_SESSION_ID", SID_A)
    _feed_stdin(monkeypatch, "")
    rc = hook_mod.activity()
    assert rc == 0
    new = registry.read(SID_A)["last_activity_at"]
    assert new > old


def test_session_start_reads_stdin_payload(monkeypatch):
    _clear_env(monkeypatch)
    payload = {
        "session_id": SID_A,
        "cwd": "/tmp/from-stdin",
        "hook_event_name": "SessionStart",
        "transcript_path": "/tmp/x.jsonl",
    }
    _feed_stdin(monkeypatch, json.dumps(payload))
    rc = hook_mod.session_start()
    assert rc == 0
    rec = registry.read(SID_A)
    assert rec is not None
    assert rec["cwd"] == "/tmp/from-stdin"


def test_activity_reads_stdin_payload(monkeypatch):
    _clear_env(monkeypatch)
    _feed_stdin(monkeypatch, json.dumps({"session_id": SID_A}))
    rc = hook_mod.activity()
    assert rc == 0
    assert registry.read(SID_A) is not None


def test_stdin_takes_priority_over_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_SESSION_ID", SID_A)  # should be IGNORED
    _feed_stdin(
        monkeypatch,
        json.dumps({"session_id": SID_B, "cwd": "/tmp/b"}),
    )
    rc = hook_mod.session_start()
    assert rc == 0
    assert registry.read(SID_B) is not None
    assert registry.read(SID_A) is None


def test_distinct_session_ids_create_distinct_records(monkeypatch):
    _clear_env(monkeypatch)
    for sid, cwd in [(SID_A, "/tmp/a"), (SID_B, "/tmp/b")]:
        _feed_stdin(
            monkeypatch,
            json.dumps({"session_id": sid, "cwd": cwd}),
        )
        assert hook_mod.session_start() == 0
    assert registry.read(SID_A)["cwd"] == "/tmp/a"
    assert registry.read(SID_B)["cwd"] == "/tmp/b"
    assert len(list(registry.iter_records())) == 2
