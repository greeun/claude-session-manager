"""``cst watch`` — auto-refreshing TUI over the registry.

Rich-based Live view with arrow-key navigation. Keybindings:
  ↑ / ↓  move highlight
  Enter  focus selected session's terminal
  r      resume selected session in new terminal
  n      edit note (prompted)
  p      cycle priority high→medium→low
  s      cycle status in_progress→blocked→waiting
  d      mark done
  a      archive
  q      quit

``cst watch --pin`` opens a new iTerm2 window of fixed size and runs
``cst watch`` inside it.
"""
from __future__ import annotations

import os
import sys
import time
import select
import shutil
import subprocess
import termios
import tty
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import registry  # noqa: E402
import livedot  # noqa: E402
import focus as focus_mod  # noqa: E402
import resume as resume_mod  # noqa: E402


PRIORITY_CYCLE = ["high", "medium", "low"]
STATUS_CYCLE = ["in_progress", "blocked", "waiting"]


def _load_rows() -> list[dict]:
    # Local import so tests that reload the registry module see the
    # current view of the filesystem, not a stale module binding.
    import registry as _registry
    try:
        rows = _registry.sorted_records(include_archived=False)
    except Exception:
        return []
    live_ttys = livedot.live_ttys()
    for r in rows:
        tty = (r.get("terminal") or {}).get("tty")
        r["_live"] = bool(tty and tty in live_ttys)
    return rows


def _mtime_key(iso: str | None) -> float:
    if not iso:
        return 0.0
    # Negative so newest-first after sort key negation.
    import datetime as _dt
    try:
        t = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        try:
            t = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return 0.0
    return t.timestamp()


def render(rows: list[dict], highlight: int) -> str:
    """Render frame as a plain-text string (used for headless tests)."""
    if not rows:
        return "(no sessions)\n"
    lines = []
    for i, r in enumerate(rows):
        dot = "●" if r.get("_live") else "○"
        pri = (r.get("priority") or "medium")[:6]
        st = (r.get("status") or "in_progress")[:12]
        sid = (r.get("session_id") or "")[:8]
        title = (r.get("title") or "")[:40]
        proj = (r.get("project_name") or "-")[:16]
        marker = "▶" if i == highlight else " "
        lines.append(f"{marker} {dot} {sid} {pri:<6} {st:<12} {title:<40} {proj}")
        lup = (r.get("last_user_prompt") or "").strip()
        if lup:
            lines.append(f"    ⤷ {lup[:100]}")
        cth = (r.get("current_task_hint") or "").strip()
        if cth:
            lines.append(f"    ⚙ {cth[:80]}")
    return "\n".join(lines) + "\n"


def _read_key(timeout: float) -> str | None:
    """Read one keystroke (single char or escape sequence). None on timeout."""
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    if not r:
        return None
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        # Escape sequence — read up to 2 more chars non-blocking.
        more = ""
        while True:
            r2, _, _ = select.select([sys.stdin], [], [], 0.01)
            if not r2:
                break
            more += sys.stdin.read(1)
        return "\x1b" + more
    return ch


def _clear() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")


def _prompt(label: str) -> str:
    """Line-edit prompt; restores raw mode after."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    termios.tcsetattr(fd, termios.TCSADRAIN, old)  # cooked
    try:
        sys.stdout.write(f"\n{label}: ")
        sys.stdout.flush()
        return sys.stdin.readline().rstrip("\n")
    finally:
        tty.setcbreak(fd)


def run(refresh_interval: float = 2.0) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write("cst watch: requires a TTY\n")
        return 2
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    highlight = 0
    last_refresh = 0.0
    rows: list[dict] = []
    try:
        while True:
            now = time.monotonic()
            if now - last_refresh >= refresh_interval:
                rows = _load_rows()
                last_refresh = now
                if rows:
                    highlight = min(highlight, len(rows) - 1)
                else:
                    highlight = 0
            _clear()
            sys.stdout.write(
                f"cst watch — {len(rows)} session(s)  "
                "↑↓ Enter=focus r=resume n=note p=pri s=status d=done a=archive q=quit\n\n"
            )
            sys.stdout.write(render(rows, highlight))
            sys.stdout.flush()

            key = _read_key(min(0.25, refresh_interval))
            if key is None:
                continue
            if key == "q":
                return 0
            if key in ("\x1b[A",) and highlight > 0:
                highlight -= 1
            elif key in ("\x1b[B",) and highlight < len(rows) - 1:
                highlight += 1
            elif rows and key in ("\r", "\n"):
                sel = rows[highlight]
                subprocess.run(
                    [sys.executable, str(_HERE / "cst.py"), "focus", sel["session_id"]],
                    check=False,
                )
                time.sleep(0.5)
            elif rows and key == "r":
                sel = rows[highlight]
                subprocess.run(
                    [sys.executable, str(_HERE / "cst.py"), "resume", sel["session_id"]],
                    check=False,
                )
                time.sleep(0.5)
            elif rows and key == "n":
                note = _prompt("note")
                tty.setcbreak(fd)
                registry.update(rows[highlight]["session_id"], note=note)
                last_refresh = 0
            elif rows and key == "p":
                cur = rows[highlight].get("priority") or "medium"
                nxt = PRIORITY_CYCLE[(PRIORITY_CYCLE.index(cur) + 1) % 3] if cur in PRIORITY_CYCLE else "medium"
                registry.update(rows[highlight]["session_id"], priority=nxt)
                last_refresh = 0
            elif rows and key == "s":
                cur = rows[highlight].get("status") or "in_progress"
                nxt = STATUS_CYCLE[(STATUS_CYCLE.index(cur) + 1) % 3] if cur in STATUS_CYCLE else "in_progress"
                registry.update(rows[highlight]["session_id"], status=nxt)
                last_refresh = 0
            elif rows and key == "d":
                registry.update(rows[highlight]["session_id"], status="done")
                last_refresh = 0
            elif rows and key == "a":
                import datetime as _dt
                registry.update(
                    rows[highlight]["session_id"],
                    archived=True,
                    archived_at=registry._utc_now_iso(),
                )
                last_refresh = 0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        _clear()


# --- cst watch --pin ------------------------------------------------------- #

_PIN_APPLESCRIPT = '''tell application "iTerm2"
    create window with default profile
    tell current session of current window
        write text {cmd}
    end tell
    set bounds of current window to {{100, 100, 900, 600}}
    activate
end tell
'''


def pin_in_iterm() -> int:
    """Open a new iTerm2 window running ``cst watch``. macOS + iTerm2 only."""
    if sys.platform != "darwin":
        sys.stderr.write("cst watch --pin: only supported on macOS\n")
        return 6
    if not shutil.which("osascript"):
        sys.stderr.write("cst watch --pin: osascript not found\n")
        return 6
    # Check iTerm2 is installed.
    check = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to return exists application process "iTerm2"'],
        capture_output=True, text=True,
    )
    # Fall back to launching it regardless; osascript will error cleanly.
    cmd = f'"cst watch"'
    script = _PIN_APPLESCRIPT.format(cmd=cmd)
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"cst watch --pin: iTerm2 not available ({r.stderr.strip()})\n")
        return 6
    return 0
