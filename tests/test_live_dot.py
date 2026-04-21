from __future__ import annotations

import os

import pytest

import livedot
import registry


SID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _make_record(tty: str | None) -> dict:
    rec = registry.new_record(SID_A)
    rec["terminal"]["tty"] = tty
    return rec


PS_HEADER = "  PID TTY      COMM\n"


def test_live_dot_marks_matching_tty(monkeypatch):
    blob = PS_HEADER + (
        "  123 ttys003  /Applications/Claude/claude\n"
        "  456 ttys004  /usr/bin/vim\n"
    )
    monkeypatch.setattr(livedot, "_run_ps", lambda: blob)
    live = livedot.live_ttys()
    assert "/dev/ttys003" in live
    assert "/dev/ttys004" not in live
    rec = _make_record("/dev/ttys003")
    assert livedot.is_live(rec, live) is True
    rec2 = _make_record("/dev/ttys999")
    assert livedot.is_live(rec2, live) is False


def test_live_dot_default_when_no_tty_stored(monkeypatch):
    blob = PS_HEADER + "  123 ttys003  /Applications/Claude/claude\n"
    monkeypatch.setattr(livedot, "_run_ps", lambda: blob)
    live = livedot.live_ttys()
    assert livedot.is_live(_make_record(None), live) is False
    assert livedot.is_live(_make_record(""), live) is False


def test_live_dot_degrades_silently_when_ps_fails(monkeypatch):
    def boom():
        raise FileNotFoundError("ps: not found")

    monkeypatch.setattr(livedot, "_run_ps", boom)
    # Must NOT raise; returns an empty set.
    live = livedot.live_ttys()
    assert live == set()
    # And the log file has an entry.
    log = livedot._scanner_log_path()
    assert log.exists()
    assert "ps failed" in log.read_text(encoding="utf-8")


def test_live_dot_ignores_non_claude_processes(monkeypatch):
    blob = PS_HEADER + (
        "  100 ttys001  node /path/claude-cli.js\n"       # basename != claude
        "  101 ttys002  /bin/bash\n"
        "  102 ttys003  claude\n"                          # basename == claude
    )
    monkeypatch.setattr(livedot, "_run_ps", lambda: blob)
    live = livedot.live_ttys()
    assert live == {"/dev/ttys003"}


def test_live_dot_comm_parsing_with_spaces(monkeypatch):
    # Path with a space should stay together in col 3 thanks to
    # split(None, 2).
    blob = PS_HEADER + (
        "  321 ttys010  /Applications/Claude Helper/claude\n"
    )
    monkeypatch.setattr(livedot, "_run_ps", lambda: blob)
    live = livedot.live_ttys()
    assert "/dev/ttys010" in live


def test_live_dot_skips_rows_with_question_tty(monkeypatch):
    blob = PS_HEADER + (
        "  500 ??       /Applications/Claude/claude\n"
        "  501 ?        /Applications/Claude/claude\n"
        "  502 -        /Applications/Claude/claude\n"
        "  503 ttys099  /Applications/Claude/claude\n"
    )
    monkeypatch.setattr(livedot, "_run_ps", lambda: blob)
    live = livedot.live_ttys()
    assert live == {"/dev/ttys099"}


def test_is_live_tty_match_requires_captured_pid_alive(monkeypatch):
    # Regression: macOS recycles tty numbers. A closed session's record
    # keeps tty=/dev/ttys003; if a *different* live claude later binds
    # the same tty, is_live() must not falsely mark the old record ●.
    import windows

    rec = _make_record("/dev/ttys003")
    rec["terminal"]["pid"] = 99999  # stored, but we control _pid_alive below
    live = {"/dev/ttys003"}

    # Captured pid is dead → the tty match is stale (recycled tty).
    monkeypatch.setattr(windows, "_pid_alive", lambda pid: False)
    assert livedot.is_live(rec, live) is False

    # Captured pid is alive → tty match is real.
    monkeypatch.setattr(windows, "_pid_alive", lambda pid: True)
    assert livedot.is_live(rec, live) is True


def test_is_live_tty_fallback_no_pid_legacy_record(monkeypatch):
    # Pre-0.4.3 records have tty but no pid. Preserve old behavior for
    # those — match on tty alone.
    rec = _make_record("/dev/ttys003")
    rec["terminal"].pop("pid", None)
    assert livedot.is_live(rec, {"/dev/ttys003"}) is True


def test_is_live_sid_map_bypasses_pid_check(monkeypatch):
    # claude --resume <sid> argv match is authoritative — don't second-
    # guess it with a pid probe. The resumed session's stored pid may
    # not have been refreshed yet.
    import windows

    rec = _make_record(None)
    rec["terminal"]["pid"] = 99999
    monkeypatch.setattr(windows, "_pid_alive", lambda pid: False)
    sid_map = {SID_A: "/dev/ttys010"}
    assert livedot.is_live(rec, set(), sid_map) is True


def test_live_dot_skips_malformed_pid(monkeypatch):
    blob = (
        "not a valid row\n"
        "  PID TTY      COMM\n"                     # header
        "  900 ttys200  /Applications/Claude/claude\n"
    )
    monkeypatch.setattr(livedot, "_run_ps", lambda: blob)
    live = livedot.live_ttys()
    assert live == {"/dev/ttys200"}
