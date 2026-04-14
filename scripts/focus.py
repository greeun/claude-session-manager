"""``csm focus <id>`` — bring a session's terminal window to the front.

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
KITTY_APPS = {"kitty"}


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
        f"csm: focus unsupported for terminal '{app_display}'. "
        f"Try: csm resume {short_id}\n"
    )
    return 4


def _failure_exit(app: str, short_id: str) -> int:
    sys.stderr.write(
        f"csm: focus failed ({app} window may be closed). "
        f"Try: csm resume {short_id}\n"
    )
    return 5


def _corrupt_window_id_exit(short_id: str) -> int:
    sys.stderr.write(
        f"csm: focus failed (corrupt window_id in record). "
        f"Try: csm resume {short_id}\n"
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


def _kitty_focus(record: dict) -> int:
    """Focus a kitty window via `kitty @ focus-window --match id:N`.

    Requires `allow_remote_control yes` in kitty.conf (or per-session
    via `kitty --listen-on`). Falls back to title matching on failure.
    """
    import shutil as _sh
    sid = record.get("session_id", "")
    short = sid[:8]
    term = record.get("terminal") or {}
    window_id = term.get("window_id")
    listen_on = term.get("kitty_listen_on")
    if not _sh.which("kitty"):
        return _title_match_focus(record) or _unsupported_exit("kitty (kitty CLI not found)", short)
    if not window_id:
        return _title_match_focus(record) or _unsupported_exit("kitty (no window id captured — restart the session)", short)
    cmd = ["kitty", "@"]
    if listen_on:
        cmd += ["--to", listen_on]
    cmd += ["focus-window", "--match", f"id:{window_id}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return _title_match_focus(record) or _failure_exit("kitty", short)
    if r.returncode != 0:
        return _title_match_focus(record) or _failure_exit("kitty", short)
    return 0


def _tmux_focus(record: dict) -> int:
    """Switch the current tmux client to the pane that owns this session.

    tmux focus is only meaningful when there's a tmux server running;
    the pane must still exist. Also tries to `osascript` bring the
    outer terminal to the front on macOS.
    """
    import shutil as _sh
    sid = record.get("session_id", "")
    short = sid[:8]
    term = record.get("terminal") or {}
    pane = term.get("tmux_pane")
    socket = term.get("tmux_socket")
    if not _sh.which("tmux") or not pane:
        return _title_match_focus(record) or _unsupported_exit("tmux (tmux not available or pane not captured)", short)
    base = ["tmux"]
    if socket:
        base += ["-S", socket]
    try:
        subprocess.run(
            base + ["select-pane", "-t", pane], capture_output=True, text=True, timeout=5,
        )
        subprocess.run(
            base + ["select-window", "-t", pane], capture_output=True, text=True, timeout=5,
        )
        r = subprocess.run(
            base + ["switch-client", "-t", pane], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return _title_match_focus(record) or _failure_exit("tmux", short)
    # switch-client rc!=0 is common when no client is attached — not fatal.
    # Try to bring the outer terminal app forward, best-effort.
    outer = term.get("app")
    if sys.platform == "darwin" and outer:
        _run_osascript(["-e", f'tell application "{outer}" to activate'])
    return 0


def _title_match_focus(record: dict) -> int | None:
    """Universal fallback: match the window whose title contains csm:<short>.

    Relies on the SessionStart hook having stamped the terminal title
    via OSC-0 (``hooks._stamp_window_title``). Returns 0 on success,
    None when this platform/tool combination can't do it (caller
    should continue with its own error path).
    """
    import shutil as _sh
    sid = record.get("session_id", "")
    short = sid[:8]
    marker = f"csm:{short}"
    if sys.platform == "darwin":
        # AppleScript iterates every process's windows; matching by
        # `name contains "csm:..."` is O(n windows) but cheap.
        script = (
            'tell application "System Events"\n'
            '  repeat with p in (every application process whose visible is true)\n'
            '    try\n'
            f'      set wins to (every window of p whose name contains "{marker}")\n'
            '      if (count of wins) > 0 then\n'
            '        set frontmost of p to true\n'
            '        tell p to perform action "AXRaise" of (item 1 of wins)\n'
            '        return "ok"\n'
            '      end if\n'
            '    end try\n'
            '  end repeat\n'
            'end tell\n'
            'return "notfound"'
        )
        try:
            r = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and "ok" in (r.stdout or ""):
                return 0
        except (subprocess.SubprocessError, OSError):
            pass
        return None
    # Linux X11
    if _sh.which("wmctrl"):
        try:
            r = subprocess.run(
                ["wmctrl", "-a", marker], capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return 0
        except (subprocess.SubprocessError, OSError):
            pass
    if _sh.which("xdotool"):
        try:
            ids = subprocess.run(
                ["xdotool", "search", "--name", marker],
                capture_output=True, text=True, timeout=5,
            )
            first = (ids.stdout or "").strip().splitlines()[:1]
            if first:
                subprocess.run(
                    ["xdotool", "windowactivate", first[0]],
                    capture_output=True, text=True, timeout=5,
                )
                return 0
        except (subprocess.SubprocessError, OSError, IndexError):
            pass
    # Wayland/sway
    if _sh.which("swaymsg"):
        try:
            r = subprocess.run(
                ["swaymsg", f'[title="{marker}"] focus'],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return 0
        except (subprocess.SubprocessError, OSError):
            pass
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
    """Execute focus for ``record``. Returns the exit code.

    Strategy:
      1. If inside tmux (pane captured), try tmux first.
      2. Then try the terminal app's native IPC (WezTerm / kitty /
         iTerm2 / Terminal.app).
      3. Universal fallback: title-match the window stamped with
         ``csm:<short-id>`` by the SessionStart hook (works on macOS
         System Events, X11 wmctrl/xdotool, Wayland sway).
      4. Give up: tell the user to try ``csm resume``.
    """
    sid = record.get("session_id", "")
    short = sid[:8]
    term = record.get("terminal") or {}
    app = term.get("app")
    app_display = app if app else "unknown"

    # (1) tmux wins when present — it's the actual owner of the pane.
    if term.get("tmux_pane"):
        rc = _tmux_focus(record)
        if rc == 0:
            return 0

    # (2) Native IPC by outer terminal app.
    if app in WEZTERM_APPS:
        rc = _wezterm_focus(record)
        if rc == 0:
            return 0
    elif app in KITTY_APPS:
        rc = _kitty_focus(record)
        if rc == 0:
            return 0
    elif app in ITERM_APPS or app in TERMINAL_APPS:
        try:
            script = build_applescript(record)
        except ValueError:
            return _corrupt_window_id_exit(short)
        if script is not None:
            rc = _run_osascript(["-e", script])
            if rc == 0:
                return 0

    # (3) Universal title-match fallback.
    rc = _title_match_focus(record)
    if rc == 0:
        return 0

    # (4) Supported-but-broken vs genuinely unsupported.
    if app in ITERM_APPS | TERMINAL_APPS | WEZTERM_APPS | KITTY_APPS:
        return _failure_exit(app_display, short)
    return _unsupported_exit(app_display, short)
