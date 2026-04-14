"""``cst watch`` — curses-based interactive TUI.

Keybindings:
    ↑ / ↓ / k / j   move highlight
    PgUp / PgDn     page
    Home / End      first / last
    Enter           focus selected session's terminal
    r               resume selected session in new terminal
    n               edit note (prompt)
    p               cycle priority high→medium→low
    s               cycle status in_progress→blocked→waiting
    d               mark done
    a               archive
    /               filter by substring
    ?               help overlay
    q / Esc         quit

``cst watch --pin`` opens a new iTerm2 window running ``cst watch``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


PRIORITY_CYCLE = ["high", "medium", "low"]
STATUS_CYCLE = ["in_progress", "blocked", "waiting"]
REFRESH_SECONDS = 2.0


def _load_rows() -> list[dict]:
    import registry as _registry
    import livedot as _livedot
    try:
        rows = _registry.sorted_records(include_archived=False)
    except Exception:
        return []
    live_ttys = _livedot.live_ttys()
    for r in rows:
        tty = (r.get("terminal") or {}).get("tty")
        r["_live"] = bool(tty and tty in live_ttys)
    return rows


def _relative_time(iso: str | None) -> str:
    import datetime as _dt
    import registry as _registry
    if not iso:
        return "-"
    t = _registry.parse_iso_z(iso) if isinstance(iso, str) else None
    if t is None:
        return "-"
    sec = int((_dt.datetime.now(_dt.timezone.utc) - t).total_seconds())
    if sec < 0:
        sec = 0
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def render(rows: list[dict], highlight: int) -> str:
    """Plain-text frame — used by tests (no curses)."""
    if not rows:
        return "(no sessions)\n"
    lines = []
    for i, r in enumerate(rows):
        marker = "▶" if i == highlight else " "
        dot = "●" if r.get("_live") else "○"
        pri = (r.get("priority") or "medium")[:6]
        st = (r.get("status") or "in_progress")[:12]
        sid = (r.get("session_id") or "")[:8]
        title = (r.get("title") or "")[:40]
        proj = (r.get("project_name") or "-")[:16]
        lines.append(f"{marker} {dot} {sid} {pri:<6} {st:<12} {title:<40} {proj}")
        lup = (r.get("last_user_prompt") or "").strip()
        if lup:
            lines.append(f"    ⤷ {lup[:100]}")
        cth = (r.get("current_task_hint") or "").strip()
        if cth:
            lines.append(f"    ⚙ {cth[:80]}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Curses UI
# --------------------------------------------------------------------------- #


def _safe_addnstr(stdscr, y, x, s, n, attr=0):
    import curses
    try:
        stdscr.addnstr(y, x, s, max(0, n), attr)
    except curses.error:
        pass


def _cell_width(s: str) -> int:
    from unicodedata import east_asian_width
    return sum(2 if east_asian_width(c) in ("W", "F") else 1 for c in s)


def _truncate(s: str, width: int) -> str:
    """Truncate to fit within `width` cells (treats wide chars as 2)."""
    if width <= 0:
        return ""
    from unicodedata import east_asian_width
    out = []
    used = 0
    for ch in s:
        w = 2 if east_asian_width(ch) in ("W", "F") else 1
        if used + w > width - 1 and _cell_width(s) > width:
            out.append("…")
            return "".join(out)
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out)


def _pad(s: str, width: int) -> str:
    """Left-align s to exactly `width` cells (truncate + pad with spaces)."""
    t = _truncate(s, width)
    return t + " " * max(0, width - _cell_width(t))


def _run_subcommand(stdscr, cmd: list[str]) -> None:
    """Run a cst subcommand outside curses so it can print to terminal."""
    import curses
    curses.endwin()
    try:
        subprocess.run(cmd, check=False)
        time.sleep(0.3)
    finally:
        stdscr.refresh()


def _prompt(stdscr, label: str, preset: str = "") -> str | None:
    """Inline single-line prompt at the bottom of the screen.

    Returns the entered string, or None on Esc.
    """
    import curses
    h, w = stdscr.getmaxyx()
    buf = list(preset)
    cur = len(buf)
    while True:
        line = f"{label}: " + "".join(buf)
        stdscr.move(h - 1, 0)
        stdscr.clrtoeol()
        _safe_addnstr(stdscr, h - 1, 0, line[: w - 1], w - 1, curses.A_BOLD)
        stdscr.move(h - 1, min(w - 1, len(label) + 2 + cur))
        stdscr.refresh()
        k = stdscr.get_wch()
        if isinstance(k, str):
            if k in ("\x1b",):
                return None
            if k in ("\n", "\r"):
                return "".join(buf)
            if k == "\x7f" or k == "\b":
                if cur > 0:
                    buf.pop(cur - 1)
                    cur -= 1
            else:
                buf.insert(cur, k)
                cur += 1
        elif isinstance(k, int):
            if k in (curses.KEY_BACKSPACE,):
                if cur > 0:
                    buf.pop(cur - 1)
                    cur -= 1
            elif k == curses.KEY_LEFT and cur > 0:
                cur -= 1
            elif k == curses.KEY_RIGHT and cur < len(buf):
                cur += 1


def _apply_filter(rows: list[dict], q: str) -> list[dict]:
    if not q:
        return rows
    ql = q.lower()
    out = []
    for r in rows:
        hay = " ".join(
            str(r.get(k) or "")
            for k in ("session_id", "title", "project_name", "last_user_prompt", "current_task_hint", "note")
        ).lower()
        if ql in hay:
            out.append(r)
    return out


def _help_overlay(stdscr) -> None:
    import curses
    lines = [
        "cst watch — keybindings",
        "",
        "  ↑/↓/k/j   move highlight",
        "  PgUp/PgDn page    Home/End first/last",
        "  Enter     focus selected session's window",
        "  r         resume in a new terminal",
        "  n         edit note     p  cycle priority",
        "  s         cycle status  d  mark done",
        "  a         archive       /  filter",
        "  ?         this help     q/Esc quit",
        "",
        "  Press any key to close.",
    ]
    h, w = stdscr.getmaxyx()
    bw = min(52, w - 4)
    bh = len(lines) + 2
    y0 = max(0, (h - bh) // 2)
    x0 = max(0, (w - bw) // 2)
    win = curses.newwin(bh, bw, y0, x0)
    win.box()
    for i, line in enumerate(lines):
        win.addnstr(1 + i, 2, line, bw - 4, curses.A_BOLD if i == 0 else 0)
    win.refresh()
    win.getch()
    del win
    stdscr.touchwin()
    stdscr.refresh()


def _tui(stdscr):
    import curses
    import datetime as _dt
    import registry as _registry

    curses.curs_set(0)
    try:
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)   # selection
        curses.init_pair(2, curses.COLOR_YELLOW, -1)                 # header
        curses.init_pair(3, curses.COLOR_GREEN, -1)                  # id / live
        curses.init_pair(4, curses.COLOR_BLUE, -1)                   # project
        curses.init_pair(5, curses.COLOR_RED, -1)                    # high priority / stale
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)                # status
    except curses.error:
        pass
    stdscr.keypad(True)
    stdscr.timeout(int(REFRESH_SECONDS * 1000))

    sel = 0
    top = 0
    filt = ""
    last_refresh = 0.0
    all_rows: list[dict] = []
    rows: list[dict] = []
    force_refresh = True

    while True:
        now = time.monotonic()
        if force_refresh or now - last_refresh >= REFRESH_SECONDS:
            all_rows = _load_rows()
            rows = _apply_filter(all_rows, filt)
            last_refresh = now
            force_refresh = False
            if rows and sel >= len(rows):
                sel = len(rows) - 1

        stdscr.erase()
        h, w = stdscr.getmaxyx()

        filt_hint = f"  filter: {filt!r}" if filt else ""
        header = (
            f" cst watch  {len(rows)}/{len(all_rows)}{filt_hint}   "
            "↑↓ Enter=focus  r=resume  n=note  p=pri  s=status  d=done  a=archive  /=filter  ?=help  q=quit "
        )
        _safe_addnstr(stdscr, 0, 0, header.ljust(w), w, curses.color_pair(2) | curses.A_BOLD)

        col_hdr = f"  {'ID':<8}  {'PRI':<6} {'STATUS':<12} {'TITLE':<44}  {'PROJECT':<18}  WHEN"
        _safe_addnstr(stdscr, 1, 0, col_hdr, w - 1, curses.A_DIM | curses.A_UNDERLINE)

        list_top = 2
        list_h = max(1, h - list_top - 1)
        # Each session uses 1–3 lines; keep it simple and treat 1 row each
        # with truncated title — sub-rows only shown for the highlight.
        if sel < top:
            top = sel
        if sel >= top + list_h:
            top = sel - list_h + 1

        y = list_top
        for i in range(list_h):
            idx = top + i
            if idx >= len(rows):
                break
            r = rows[idx]
            dot = "●" if r.get("_live") else "○"
            pri = r.get("priority") or "medium"
            st = r.get("status") or "in_progress"
            sid = (r.get("session_id") or "")[:8]
            title = (r.get("title") or "").replace("\n", " ")
            proj = (r.get("project_name") or "-")[:18]
            rel = _relative_time(r.get("last_activity_at"))
            is_sel = idx == sel
            base = curses.color_pair(1) | curses.A_BOLD if is_sel else 0
            # Render row
            line = (
                f"{dot} {_pad(sid, 8)}  {_pad(pri, 6)} {_pad(st, 12)} "
                f"{_pad(title, 44)}  {_pad(proj, 18)}  {_pad(rel, 8)}"
            )
            _safe_addnstr(stdscr, y, 0, line.ljust(w - 1), w - 1, base)
            y += 1

        # Detail panel at bottom (3 lines) for the selected row.
        if rows and 0 <= sel < len(rows):
            r = rows[sel]
            lup = (r.get("last_user_prompt") or "").strip().replace("\n", " ")
            las = (r.get("last_assistant_summary") or "").strip().replace("\n", " ")
            cth = (r.get("current_task_hint") or "").strip().replace("\n", " ")
            note = (r.get("note") or "").strip().replace("\n", " ")
            footer_y = h - 1
            parts = []
            if lup:
                parts.append(f"⤷ {lup}")
            if cth:
                parts.append(f"⚙ {cth}")
            if note:
                parts.append(f"✎ {note}")
            text = "   ".join(parts) if parts else (las or "")
            _safe_addnstr(stdscr, footer_y, 0, _truncate(text, w - 1), w - 1, curses.A_DIM)

        stdscr.refresh()

        try:
            k = stdscr.get_wch()
        except curses.error:
            continue

        if isinstance(k, str):
            if k in ("q", "\x1b"):
                return
            if k == "?":
                _help_overlay(stdscr)
                force_refresh = True
            elif k == "/":
                new = _prompt(stdscr, "filter", filt)
                if new is not None:
                    filt = new
                    sel = 0
                    top = 0
                    force_refresh = True
            elif k in ("j",) and rows and sel < len(rows) - 1:
                sel += 1
            elif k in ("k",) and rows and sel > 0:
                sel -= 1
            elif rows and k in ("\n", "\r"):
                _run_subcommand(stdscr, ["cst", "focus", rows[sel]["session_id"]])
                force_refresh = True
            elif rows and k == "r":
                _run_subcommand(stdscr, ["cst", "resume", rows[sel]["session_id"]])
                force_refresh = True
            elif rows and k == "n":
                new = _prompt(stdscr, "note", rows[sel].get("note") or "")
                if new is not None:
                    _registry.update(rows[sel]["session_id"], note=new)
                    force_refresh = True
            elif rows and k == "p":
                cur = rows[sel].get("priority") or "medium"
                nxt = PRIORITY_CYCLE[(PRIORITY_CYCLE.index(cur) + 1) % 3] if cur in PRIORITY_CYCLE else "medium"
                _registry.update(rows[sel]["session_id"], priority=nxt)
                force_refresh = True
            elif rows and k == "s":
                cur = rows[sel].get("status") or "in_progress"
                nxt = STATUS_CYCLE[(STATUS_CYCLE.index(cur) + 1) % 3] if cur in STATUS_CYCLE else "in_progress"
                _registry.update(rows[sel]["session_id"], status=nxt)
                force_refresh = True
            elif rows and k == "d":
                _registry.update(rows[sel]["session_id"], status="done")
                force_refresh = True
            elif rows and k == "a":
                _registry.update(
                    rows[sel]["session_id"],
                    archived=True,
                    archived_at=_registry._utc_now_iso(),
                )
                force_refresh = True
        elif isinstance(k, int):
            if k == curses.KEY_UP and rows and sel > 0:
                sel -= 1
            elif k == curses.KEY_DOWN and rows and sel < len(rows) - 1:
                sel += 1
            elif k == curses.KEY_PPAGE:
                sel = max(0, sel - max(1, list_h - 1))
            elif k == curses.KEY_NPAGE:
                sel = min(len(rows) - 1, sel + max(1, list_h - 1)) if rows else 0
            elif k == curses.KEY_HOME:
                sel = 0
            elif k == curses.KEY_END and rows:
                sel = len(rows) - 1


def run(refresh_interval: float = REFRESH_SECONDS) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write("cst watch: requires a TTY\n")
        return 2
    import curses
    try:
        curses.wrapper(_tui)
    except KeyboardInterrupt:
        pass
    return 0


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
    if sys.platform != "darwin":
        sys.stderr.write("cst watch --pin: only supported on macOS\n")
        return 6
    if not shutil.which("osascript"):
        sys.stderr.write("cst watch --pin: osascript not found\n")
        return 6
    cmd = '"cst watch"'
    script = _PIN_APPLESCRIPT.format(cmd=cmd)
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"cst watch --pin: iTerm2 not available ({r.stderr.strip()})\n")
        return 6
    return 0
