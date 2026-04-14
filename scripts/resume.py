"""``csm resume <id>`` — spawn a new terminal window that runs
``cd <cwd> && claude --resume <session_id>``.

Per sprint_contract.md §2.8. Two escape layers apply — see §2.8 for
the binding rules.
"""

from __future__ import annotations

import shlex
import subprocess
import sys

from focus import _run_osascript  # reuse the monkeypatchable helper

# -- Pinned AppleScript templates (see §2.8) --------------------------------

ITERM_RESUME = '''\
tell application "iTerm2"
    activate
    create window with default profile
    tell current session of current window
        write text {shell_cmd}
    end tell
end tell'''

TERMINAL_RESUME = '''\
tell application "Terminal"
    activate
    do script {shell_cmd}
end tell'''


_UUID_CHARS = set("0123456789abcdef-")


def _validate_session_id(sid: str) -> None:
    if not isinstance(sid, str) or len(sid) != 36:
        raise ValueError("session_id must be 36-char UUID")
    if not all(c in _UUID_CHARS for c in sid):
        raise ValueError("session_id has invalid characters")


def _validate_cwd(cwd: str) -> None:
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("cwd is empty")
    if "\x00" in cwd or "\n" in cwd:
        raise ValueError("cwd contains null byte or newline")


def _applescript_quote(value: str) -> str:
    """Wrap ``value`` as an AppleScript string literal.

    Only ``\\`` and ``"`` need escaping inside a quoted literal.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def _build_shell_command(cwd: str, session_id: str) -> str:
    """POSIX-safe command string for the shell layer."""
    _validate_cwd(cwd)
    _validate_session_id(session_id)
    return f"cd {shlex.quote(cwd)} && claude --resume {shlex.quote(session_id)}"


def _is_iterm2_installed() -> bool:
    """Probe whether iTerm2 is installed on this machine."""
    try:
        r = subprocess.run(
            ["osascript", "-e", 'id of application "iTerm2"'],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def _is_terminal_app_installed() -> bool:
    try:
        r = subprocess.run(
            ["osascript", "-e", 'id of application "Terminal"'],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def build_applescript(record: dict, *, prefer: str | None = None) -> str:
    """Return the AppleScript program to resume ``record``.

    ``prefer`` overrides the terminal choice for tests. Valid values:
    ``"iterm"``, ``"terminal"``.
    """
    sid = record["session_id"]
    cwd = record.get("cwd") or ""
    shell_cmd = _build_shell_command(cwd, sid)
    quoted = _applescript_quote(shell_cmd)

    if prefer == "iterm":
        return ITERM_RESUME.format(shell_cmd=quoted)
    if prefer == "terminal":
        return TERMINAL_RESUME.format(shell_cmd=quoted)
    # Auto: try iTerm2 first, fall back to Terminal.app.
    if _is_iterm2_installed():
        return ITERM_RESUME.format(shell_cmd=quoted)
    if _is_terminal_app_installed():
        return TERMINAL_RESUME.format(shell_cmd=quoted)
    raise RuntimeError("no supported terminal")


def _wezterm_resume(cwd: str, sid: str) -> int:
    """Open a new WezTerm window running `claude --resume <sid>`."""
    import shutil as _sh
    if not _sh.which("wezterm"):
        return 1
    try:
        r = subprocess.run(
            [
                "wezterm", "cli", "spawn", "--new-window",
                "--cwd", cwd, "--", "claude", "--resume", sid,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return 1
    if r.returncode != 0:
        return 1
    # Bring WezTerm to the front.
    _run_osascript(["-e", 'tell application "WezTerm" to activate'])
    return 0


def run(record: dict) -> int:
    cwd = record.get("cwd") or ""
    sid = record.get("session_id") or ""
    if not cwd:
        sys.stderr.write("csm: cannot resume: no cwd recorded for this session\n")
        return 1
    try:
        _validate_cwd(cwd)
        _validate_session_id(sid)
    except ValueError as e:
        sys.stderr.write(f"csm: cannot resume: {e}\n")
        return 1

    app = (record.get("terminal") or {}).get("app")

    # Prefer the terminal the session originally used so the user sees it.
    if app == "WezTerm" and _wezterm_resume(cwd, sid) == 0:
        return 0

    # iTerm2 / Terminal.app via AppleScript.
    try:
        script = build_applescript(record)
    except ValueError as e:
        sys.stderr.write(f"csm: cannot resume: {e}\n")
        return 1
    except RuntimeError:
        # No AppleScript target found — try WezTerm as last resort.
        if _wezterm_resume(cwd, sid) == 0:
            return 0
        sys.stderr.write(
            "csm: no supported terminal for resume; install iTerm2, Terminal.app, or WezTerm\n"
        )
        return 4
    rc = _run_osascript(["-e", script])
    if rc != 0:
        # AppleScript failed — try WezTerm fallback.
        if _wezterm_resume(cwd, sid) == 0:
            return 0
        sys.stderr.write("csm: resume failed (osascript non-zero exit)\n")
        return 5
    return 0
