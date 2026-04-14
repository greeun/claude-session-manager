from __future__ import annotations

import pytest

import registry
import resume as resume_mod
import focus as focus_mod


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
    # resume.py imports _run_osascript from focus; patch both.
    monkeypatch.setattr(focus_mod, "_run_osascript", c)
    monkeypatch.setattr(resume_mod, "_run_osascript", c)
    return c


def _rec(cwd="/tmp/demo"):
    r = registry.new_record(SID)
    r["cwd"] = cwd
    return r


def test_resume_builds_cd_and_claude_resume_command(cap, monkeypatch):
    # Force iTerm2 branch deterministically.
    rec = _rec("/tmp/demo")
    script = resume_mod.build_applescript(rec, prefer="iterm")
    assert "cd " in script
    assert "claude --resume" in script
    assert SID in script


def test_resume_iterm_osascript_string_matches_template(cap):
    """Byte-pin against the iTerm2 resume template. Uses a cwd with a
    space so shlex.quote produces a deterministic single-quoted form."""
    cwd_with_space = "/tmp/my demo"
    rec = _rec(cwd_with_space)
    script = resume_mod.build_applescript(rec, prefer="iterm")
    expected_shell = f"cd '/tmp/my demo' && claude --resume {SID}"
    # SID matches shlex.quote's no-quote safe-char set; no wrapping.
    expected_applescript_arg = '"' + expected_shell + '"'
    expected = (
        'tell application "iTerm2"\n'
        "    activate\n"
        "    create window with default profile\n"
        "    tell current session of current window\n"
        f"        write text {expected_applescript_arg}\n"
        "    end tell\n"
        "end tell"
    )
    assert script == expected


def test_resume_terminal_app_osascript_string_matches_template(cap):
    cwd_with_space = "/tmp/my demo"
    rec = _rec(cwd_with_space)
    script = resume_mod.build_applescript(rec, prefer="terminal")
    expected_shell = f"cd '/tmp/my demo' && claude --resume {SID}"
    expected_applescript_arg = '"' + expected_shell + '"'
    expected = (
        'tell application "Terminal"\n'
        "    activate\n"
        f"    do script {expected_applescript_arg}\n"
        "end tell"
    )
    assert script == expected


def test_resume_no_cwd_exits_1(capsys):
    r = registry.new_record(SID)
    r["cwd"] = ""
    rc = resume_mod.run(r)
    assert rc == 1
    err = capsys.readouterr().err
    assert "no cwd recorded" in err


def test_resume_no_supported_terminal_exits_4(monkeypatch, capsys):
    monkeypatch.setattr(resume_mod, "_is_iterm2_installed", lambda: False)
    monkeypatch.setattr(resume_mod, "_is_terminal_app_installed", lambda: False)
    monkeypatch.setattr(resume_mod, "_wezterm_resume", lambda cwd, sid: 1)
    rec = _rec("/tmp/demo")
    rc = resume_mod.run(rec)
    assert rc == 4
    err = capsys.readouterr().err
    assert "no supported terminal" in err


@pytest.mark.parametrize(
    "cwd",
    ["/tmp/a b", "/tmp/a'b", '/tmp/a"b', "/tmp/a$(x)b", "/tmp/a;b"],
)
def test_resume_shell_escapes_cwd(cwd):
    """shlex.quote must sanitise every dangerous shell meta character."""
    rec = _rec(cwd)
    shell_cmd = resume_mod._build_shell_command(cwd, SID)
    # Dangerous chars must appear only inside single-quoted form; the
    # embedded single quote case produces shell's '"'"' escape.
    import shlex
    expected = f"cd {shlex.quote(cwd)} && claude --resume {shlex.quote(SID)}"
    assert shell_cmd == expected


def test_resume_applescript_escapes_backslash_and_quote():
    weird = '/tmp/has"quote\\and-backslash'
    quoted = resume_mod._applescript_quote(weird)
    # Within the outer double quotes, '"' becomes '\"' and '\' becomes '\\'.
    assert quoted.startswith('"') and quoted.endswith('"')
    assert '\\"' in quoted
    assert '\\\\' in quoted
    # And no un-escaped double-quote inside:
    inner = quoted[1:-1]
    # Count of backslash-quote pairs equals original " count.
    assert inner.count('\\"') == weird.count('"')


def test_resume_rejects_cwd_with_newline_or_null(capsys):
    r = registry.new_record(SID)
    r["cwd"] = "/tmp/bad\npath"
    rc = resume_mod.run(r)
    assert rc == 1
    r2 = registry.new_record(SID)
    r2["cwd"] = "/tmp/bad\x00path"
    rc2 = resume_mod.run(r2)
    assert rc2 == 1
