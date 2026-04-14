from __future__ import annotations

import re

import pytest

import focus as focus_mod
import registry


SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000"


class _Capture:
    def __init__(self, rc: int = 0):
        self.rc = rc
        self.args: list[list[str]] = []

    def __call__(self, args: list[str]) -> int:
        self.args.append(list(args))
        return self.rc


@pytest.fixture
def cap(monkeypatch):
    c = _Capture()
    monkeypatch.setattr(focus_mod, "_run_osascript", c)
    return c


def _rec(app, window_id=None, tab_id=None, tty="/dev/ttys001"):
    r = registry.new_record(SID)
    r["terminal"]["app"] = app
    r["terminal"]["window_id"] = window_id
    r["terminal"]["tab_id"] = tab_id
    r["terminal"]["tty"] = tty
    return r


def test_focus_iterm_runs_expected_osascript_args(cap):
    rec = _rec("iTerm2", window_id=42)
    assert focus_mod.run(rec) == 0
    assert len(cap.args) == 1
    assert cap.args[0][0] == "-e"


def test_focus_iterm_osascript_string_matches_template(cap):
    rec = _rec("iTerm2", window_id=123)
    focus_mod.run(rec)
    script = cap.args[0][1]
    expected = (
        'tell application "iTerm2"\n'
        "    activate\n"
        "    tell window id 123 to select\n"
        "end tell"
    )
    assert script == expected


def test_focus_iterm_no_window_id_falls_back_to_activate(cap):
    rec = _rec("iTerm2", window_id=None)
    assert focus_mod.run(rec) == 0
    script = cap.args[0][1]
    assert script == 'tell application "iTerm2" to activate'


def test_focus_terminal_app_runs_expected_osascript_args(cap):
    rec = _rec("Terminal", window_id=7, tab_id=2)
    focus_mod.run(rec)
    script = cap.args[0][1]
    expected = (
        'tell application "Terminal"\n'
        "    activate\n"
        "    set index of window id 7 to 1\n"
        "    tell window id 7 to set selected tab to tab 2\n"
        "end tell"
    )
    assert script == expected


def test_focus_terminal_app_osascript_string_matches_template(cap):
    # window only (no tab_id):
    rec = _rec("Apple_Terminal", window_id=5, tab_id=None)
    focus_mod.run(rec)
    assert cap.args[0][1] == (
        'tell application "Terminal"\n'
        "    activate\n"
        "    set index of window id 5 to 1\n"
        "end tell"
    )
    # no window id at all:
    cap.args.clear()
    rec2 = _rec("Terminal", window_id=None, tab_id=None)
    focus_mod.run(rec2)
    assert cap.args[0][1] == 'tell application "Terminal" to activate'


def test_focus_unsupported_app_exits_4_with_resume_hint(cap, capsys):
    rec = _rec("Ghostty")
    rc = focus_mod.run(rec)
    assert rc == 4
    assert cap.args == []  # no osascript attempted
    err = capsys.readouterr().err
    assert "unsupported" in err
    assert "csm resume" in err
    assert SID[:8] in err


def test_focus_null_app_treated_as_unsupported(cap, capsys):
    rec = _rec(None)
    rc = focus_mod.run(rec)
    assert rc == 4
    err = capsys.readouterr().err
    assert "'unknown'" in err


def test_focus_osascript_failure_exits_5_with_resume_hint(monkeypatch, capsys):
    monkeypatch.setattr(focus_mod, "_run_osascript", lambda args: 2)
    rec = _rec("iTerm2", window_id=1)
    rc = focus_mod.run(rec)
    assert rc == 5
    err = capsys.readouterr().err
    assert "failed" in err
    assert "csm resume" in err


def test_focus_window_id_is_integer_only(cap, capsys):
    # String window_id → rejected with exit 5 and the corrupt-wid message.
    rec = _rec("iTerm2", window_id="1) evil")
    rc = focus_mod.run(rec)
    assert rc == 5
    err = capsys.readouterr().err
    assert "corrupt window_id" in err
    assert cap.args == []  # no AppleScript was ever constructed

    # Bool rejected too.
    cap.args.clear()
    rec2 = _rec("iTerm2", window_id=True)
    rc2 = focus_mod.run(rec2)
    assert rc2 == 5
    assert cap.args == []

    # Dict rejected.
    rec3 = _rec("iTerm2", window_id={"x": 1})
    rc3 = focus_mod.run(rec3)
    assert rc3 == 5
