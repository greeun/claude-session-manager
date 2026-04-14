"""``cst focus <id>`` — bring a session's terminal window to the front.

Templates per sprint_contract.md §2.8 are literal and byte-asserted by
tests. ``_run_osascript`` is monkeypatched by tests — no real
AppleScript runs in CI.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable

# -- Pinned AppleScript templates (see §2.8) --------------------------------

ITERM_FOCUS_WITH_WINDOW = '''\
tell application "iTerm2"
    activate
    tell window id {w} to select
end tell'''

ITERM_FOCUS_ACTIVATE_ONLY = 'tell application "iTerm2" to activate'

TERMINAL_FOCUS_WITH_WINDOW_AND_TAB = '''\
tell application "Terminal"
    activate
    set index of window id {w} to 1
    tell window id {w} to set selected tab to tab {t}
end tell'''

TERMINAL_FOCUS_WITH_WINDOW_ONLY = '''\
tell application "Terminal"
    activate
    set index of window id {w} to 1
end tell'''

TERMINAL_FOCUS_ACTIVATE_ONLY = 'tell application "Terminal" to activate'

ITERM_APPS = {"iTerm.app", "iTerm2"}
TERMINAL_APPS = {"Apple_Terminal", "Terminal"}
WEZTERM_APPS = {"WezTerm"}


def _run_osascript(args: list[str]) -> int:
    """Run ``osascript <args>`` and return its exit code.

    Tests monkeypatch this to a capturing no-op.
    """
    try:
        r = subprocess.run(
            ["osascript", *args], capture_output=True, text=True, timeout=5
        )
        return r.returncode
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 1


def _as_int(value, field_name: str) -> int:
    """Strict int conversion. Refuses bool / None / non-numeric values.

    Raises ``ValueError`` with a clear message; caller maps to exit 5.
    """
    if value is None:
        raise ValueError(f"{field_name} is null")
    if isinstance(value, bool):  # bool is a subclass of int
        raise ValueError(f"{field_name} is a bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(
        f"{field_name} must be an integer, got {type(value).__name__}"
    )


def _unsupported_exit(app_display: str, short_id: str) -> int:
    sys.stderr.write(
        f"cst: focus unsupported for terminal '{app_display}'. "
        f"Try: cst resume {short_id}\n"
    )
    return 4


def _failure_exit(app: str, short_id: str) -> int:
    sys.stderr.write(
        f"cst: focus failed ({app} window may be closed). "
        f"Try: cst resume {short_id}\n"
    )
    return 5


def _corrupt_window_id_exit(short_id: str) -> int:
    sys.stderr.write(
        f"cst: focus failed (corrupt window_id in record). "
        f"Try: cst resume {short_id}\n"
    )
    return 5


def build_applescript(record: dict) -> str | None:
    """Return the AppleScript program string for ``record``, or None if
    the record's ``terminal.app`` is unsupported. Raises ``ValueError``
    when stored ``window_id`` / ``tab_id`` cannot be integer-coerced.
    """
    term = record.get("terminal") or {}
    app = term.get("app")
    wid = term.get("window_id")
    tab = term.get("tab_id")

    if app in ITERM_APPS:
        if wid is None:
            return ITERM_FOCUS_ACTIVATE_ONLY
        w = _as_int(wid, "window_id")
        return ITERM_FOCUS_WITH_WINDOW.format(w=w)

    if app in TERMINAL_APPS:
        if wid is None:
            return TERMINAL_FOCUS_ACTIVATE_ONLY
        w = _as_int(wid, "window_id")
        if tab is None:
            return TERMINAL_FOCUS_WITH_WINDOW_ONLY.format(w=w)
        t = _as_int(tab, "tab_id")
        return TERMINAL_FOCUS_WITH_WINDOW_AND_TAB.format(w=w, t=t)

    return None


def _wezterm_focus(record: dict) -> int:
    """Focus a WezTerm pane by pane_id stored in ``terminal.window_id``."""
    import shutil as _sh
    sid = record.get("session_id", "")
    short = sid[:8]
    term = record.get("terminal") or {}
    pane_id = term.get("window_id")
    if not pane_id:
        return _unsupported_exit("WezTerm (no pane_id captured — restart the session after installing)", short)
    if not _sh.which("wezterm"):
        return _unsupported_exit("WezTerm (wezterm CLI not found on PATH)", short)
    try:
        r = subprocess.run(
            ["wezterm", "cli", "activate-pane", "--pane-id", str(pane_id)],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return _failure_exit("WezTerm", short)
    if r.returncode != 0:
        return _failure_exit("WezTerm", short)
    return 0


def run(record: dict) -> int:
    """Execute focus for ``record``. Returns the exit code."""
    sid = record.get("session_id", "")
    short = sid[:8]
    app = (record.get("terminal") or {}).get("app")
    app_display = app if app else "unknown"
    if app in WEZTERM_APPS:
        return _wezterm_focus(record)
    try:
        script = build_applescript(record)
    except ValueError:
        return _corrupt_window_id_exit(short)
    if script is None:
        return _unsupported_exit(app_display, short)
    rc = _run_osascript(["-e", script])
    if rc != 0:
        return _failure_exit(app_display, short)
    return 0
