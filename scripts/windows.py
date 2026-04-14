"""Detect which Claude Code sessions have an open terminal window.

Works by reading the set of window titles visible on the desktop and
looking for the ``csm:<short-id>`` marker stamped by the SessionStart
hook (see ``hooks._stamp_window_title``). One query per refresh, then
match per session in Python — cheap even with many sessions.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

_MARKER_RE = re.compile(r"csm:([0-9a-f]{8})")


def _macos_titles() -> str:
    """Return concatenated window titles from every visible app via AppleScript."""
    if not shutil.which("osascript"):
        return ""
    script = (
        'tell application "System Events"\n'
        '  set out to ""\n'
        '  repeat with p in (every application process whose visible is true)\n'
        '    try\n'
        '      repeat with w in (every window of p)\n'
        '        set out to out & (name of w) & linefeed\n'
        '      end repeat\n'
        '    end try\n'
        '  end repeat\n'
        '  return out\n'
        'end tell'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _linux_x11_titles() -> str:
    if shutil.which("wmctrl"):
        try:
            r = subprocess.run(
                ["wmctrl", "-l"], capture_output=True, text=True, timeout=3,
            )
            return r.stdout if r.returncode == 0 else ""
        except (subprocess.SubprocessError, OSError):
            pass
    if shutil.which("xdotool"):
        try:
            ids = subprocess.run(
                ["xdotool", "search", "--name", "csm:"],
                capture_output=True, text=True, timeout=3,
            )
            titles: list[str] = []
            for wid in (ids.stdout or "").split():
                g = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, text=True, timeout=1,
                )
                titles.append(g.stdout.strip())
            return "\n".join(titles)
        except (subprocess.SubprocessError, OSError):
            pass
    return ""


def _sway_titles() -> str:
    if not shutil.which("swaymsg"):
        return ""
    try:
        r = subprocess.run(
            ["swaymsg", "-t", "get_tree"], capture_output=True, text=True, timeout=3,
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _wezterm_panes() -> str:
    """`wezterm cli list` titles. Works cross-platform."""
    if not shutil.which("wezterm"):
        return ""
    try:
        r = subprocess.run(
            ["wezterm", "cli", "list", "--format", "json"],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def open_short_ids() -> set[str]:
    """Return the set of 8-char short ids whose windows are currently open.

    Queries the host windowing system once and returns all matching
    short ids. An empty set means "can't tell" — callers should treat
    that as "unknown" rather than "not open".
    """
    blob = ""
    if sys.platform == "darwin":
        blob = _macos_titles()
    else:
        blob = _linux_x11_titles() or _sway_titles()
    # Always also probe WezTerm panes — some users launch WezTerm on
    # macOS with a title that System Events can't read due to AX
    # permission gaps.
    blob += "\n" + _wezterm_panes()
    return set(_MARKER_RE.findall(blob))
