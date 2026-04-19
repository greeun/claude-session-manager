"""Detect which Claude Code sessions still have a live terminal.

Primary path — **pid liveness**: every session record (created by the
``SessionStart`` hook in ``hooks._terminal_capture``) stores ``pid``,
the PID of the ancestor that owns the tty — typically the shell or
terminal emulator pane process. A cheap in-process probe (``os.kill
(pid, 0)`` on POSIX, ``OpenProcess`` on Windows) tells us whether the
terminal is still running without touching the OS windowing system.

Secondary path — **tty match**: a single ``ps`` snapshot intersecting
the registry's captured ``tty`` values, for records that predate pid
capture but do have a usable tty. One ~50 ms subprocess per refresh
regardless of the number of sessions.

Legacy title scraping is retained as a module-level helper for
on-demand features (``csm focus`` matching a short-id to a concrete
window). It is **never** called on the refresh hot path any more —
the previous implementation drove coreservicesd to 80% CPU by issuing
AppleScript into ``System Events`` every 2 seconds.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

_MARKER_RE = re.compile(r"csm:([0-9a-f]{8})")

# Sentinel tty values that are not usable as a stable session identifier.
# ``/dev/tty`` is a per-process alias for "my controlling terminal" — it
# resolves to a different real tty in every process that opens it, so it
# cannot be intersected with a ps snapshot.
_UNUSABLE_TTYS: frozenset[str] = frozenset({"/dev/tty", "tty", "?", "??", "-", ""})


# ----------------------------------------------------------------------
# Primary: pid liveness
# ----------------------------------------------------------------------

def _pid_alive(pid: int | None) -> bool:
    """Cross-platform "is this pid still running" probe. No side effects."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes
            SYNCHRONIZE = 0x00100000
            WAIT_TIMEOUT = 0x00000102
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Pid exists but belongs to another user — still "alive".
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------
# Secondary: tty liveness via one ps snapshot
# ----------------------------------------------------------------------

def _ps_active_ttys() -> set[str] | None:
    """Return the ttys currently held by at least one process.

    Returns ``None`` when ``ps`` is unavailable or produced no output,
    so callers can distinguish "couldn't tell" from "all closed".
    Names are normalised to include the ``/dev/`` prefix so they
    compare equal to what ``hooks._terminal_capture`` stores.
    """
    if sys.platform.startswith("win") or not shutil.which("ps"):
        return None
    try:
        r = subprocess.run(
            ["ps", "-A", "-o", "tty="],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    out: set[str] = set()
    for raw in (r.stdout or "").splitlines():
        tty = raw.strip()
        if not tty or tty in _UNUSABLE_TTYS:
            continue
        out.add(tty if tty.startswith("/dev/") else f"/dev/{tty}")
    return out or None


# ----------------------------------------------------------------------
# Title-scraping helpers (legacy / on-demand fallback only)
# ----------------------------------------------------------------------

def _macos_titles() -> str:
    """Concatenated window titles via AppleScript ``System Events``.

    Heavy — walks the accessibility tree of every visible app. Loads
    coreservicesd significantly. **Do not call on the refresh hot path.**
    Retained for ``csm focus`` and other on-demand lookups.
    """
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
    """``wezterm cli list`` titles. Works cross-platform (including Windows)."""
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


def _win32_titles() -> str:
    """Enumerate top-level visible window titles on Windows via ``EnumWindows``.

    stdlib only (``ctypes``) — no ``pywin32`` dependency. Requires no
    special permissions: Win32 exposes window titles to any process in
    the user's session.
    """
    if not sys.platform.startswith("win"):
        return ""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return ""
    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return ""
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
    )
    titles: list[str] = []

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value:
                titles.append(buf.value)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(_cb), 0)
    except OSError:
        return ""
    return "\n".join(titles)


def scrape_visible_titles() -> str:
    """Concatenated window titles from the host windowing system.

    Public helper for on-demand lookups (e.g. ``csm focus`` resolving a
    short-id to a concrete window). **Never called on the watch refresh
    hot path.** May block for several hundred milliseconds on macOS.
    """
    blob = ""
    if sys.platform == "darwin":
        blob = _macos_titles()
    elif sys.platform.startswith("linux"):
        blob = _linux_x11_titles() or _sway_titles()
    elif sys.platform.startswith("win"):
        blob = _win32_titles()
    # WezTerm is cross-platform and often more reliable than the host
    # window manager — always probe it when available.
    blob += "\n" + _wezterm_panes()
    return blob


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------

def _registry_sessions() -> list[tuple[str, int | None, str | None]] | None:
    """Return ``[(short_id, pid, tty), ...]`` for non-archived sessions.

    ``pid`` and ``tty`` reflect what was captured at SessionStart; either
    or both may be ``None`` for legacy records. Archived and done
    sessions are excluded — they are not candidates for "open".
    Returns ``None`` when the registry cannot be read.
    """
    try:
        from registry import sorted_records  # type: ignore
        records = sorted_records(include_archived=False)
    except Exception:
        return None
    out: list[tuple[str, int | None, str | None]] = []
    for rec in records:
        if rec.get("done_at"):
            continue
        sid = rec.get("session_id") or ""
        if len(sid) < 8:
            continue
        short = sid[:8]
        term = rec.get("terminal") or {}
        pid = term.get("pid")
        if not isinstance(pid, int):
            pid = None
        tty = term.get("tty")
        if not isinstance(tty, str) or tty in _UNUSABLE_TTYS:
            tty = None
        elif not tty.startswith("/dev/"):
            tty = f"/dev/{tty}"
        out.append((short, pid, tty))
    return out


def open_short_ids() -> set[str]:
    """Return the 8-char short ids whose terminals are currently open.

    Resolution is strictly registry-driven and cheap:

    * ``pid`` captured → probe with ``os.kill(pid, 0)`` (POSIX) or
      ``OpenProcess`` (Windows). One syscall per session.
    * No ``pid`` but ``tty`` captured → intersect with a single ``ps``
      snapshot.
    * Neither ``pid`` nor a usable ``tty`` → **not reported as open**.
      Such records are legacy pre-capture artefacts; re-attaching to
      the session (or marking it done) repopulates the fields.

    Returns an empty set when the registry cannot be read at all —
    callers should treat that as "unknown", not "definitely closed".
    """
    sessions = _registry_sessions()
    if sessions is None:
        return set()

    open_ids: set[str] = set()
    tty_pending: list[tuple[str, str]] = []

    for short, pid, tty in sessions:
        if pid is not None:
            if _pid_alive(pid):
                open_ids.add(short)
            continue
        if tty is not None:
            tty_pending.append((short, tty))

    if tty_pending:
        active = _ps_active_ttys()
        if active:
            for short, tty in tty_pending:
                if tty in active:
                    open_ids.add(short)

    return open_ids
