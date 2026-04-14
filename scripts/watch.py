"""``csm watch`` — curses-based interactive TUI.

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

``csm watch --pin`` opens a new iTerm2 window running ``csm watch``.
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
    import windows as _windows
    try:
        rows = _registry.sorted_records(include_archived=False)
    except Exception:
        return []
    live_ttys = _livedot.live_ttys()
    open_ids = _windows.open_short_ids()
    for r in rows:
        tty = (r.get("terminal") or {}).get("tty")
        short = (r.get("session_id") or "")[:8]
        r["_live"] = bool(tty and tty in live_ttys)
        r["_window_open"] = short in open_ids
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


def _fit_right(s: str, width: int) -> str:
    """Fit s into `width` cells by keeping the RIGHT side; prefix `…` if trimmed."""
    if width <= 0:
        return ""
    if _cell_width(s) <= width:
        return s + " " * (width - _cell_width(s))
    # Walk from the right, accumulating chars whose total cell width ≤ width-1.
    from unicodedata import east_asian_width
    chars: list[str] = []
    used = 0
    for ch in reversed(s):
        w = 2 if east_asian_width(ch) in ("W", "F") else 1
        if used + w > width - 1:
            break
        chars.append(ch)
        used += w
    result = "…" + "".join(reversed(chars))
    pad = width - _cell_width(result)
    return result + " " * max(0, pad)


def _marquee(s: str, width: int, offset: int) -> str:
    """Carousel-style scrolling window of s in a field of `width` cells.

    Appends a 3-space gap so the text loops with breathing room.
    `offset` is incremented by the caller each tick.
    """
    if width <= 0:
        return ""
    if _cell_width(s) <= width:
        return _pad(s, width)
    scroll = s + "   "
    from unicodedata import east_asian_width
    # Build a list of (char, cell_width).
    cells = [(c, 2 if east_asian_width(c) in ("W", "F") else 1) for c in scroll]
    total = sum(w for _, w in cells)
    off = offset % total
    # Skip chars until we've skipped `off` cells (may leave a partial wide char).
    skipped = 0
    i = 0
    while i < len(cells) and skipped < off:
        skipped += cells[i][1]
        i += 1
    # Concatenate chars cyclically up to `width` cells.
    out: list[str] = []
    used = 0
    j = i
    n = len(cells)
    while used < width:
        ch, cw = cells[j % n]
        if used + cw > width:
            break
        out.append(ch)
        used += cw
        j += 1
    return "".join(out) + " " * max(0, width - used)


_CSM_PY = _HERE / "csm.py"


def _run_csm(stdscr, args: list[str]) -> int:
    """Run `csm <args>` outside curses and return its exit code."""
    import curses
    curses.endwin()
    rc = 1
    try:
        r = subprocess.run([sys.executable, str(_CSM_PY), *args], check=False)
        rc = r.returncode
        time.sleep(0.3)
    finally:
        stdscr.refresh()
    return rc


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


def _confirm_delete(stdscr, sid: str) -> bool:
    """Prompt y/n and unlink the registry record. Returns True on delete."""
    import registry as _registry
    confirm = _prompt(stdscr, f"delete {sid[:8]}? type 'y'")
    if not confirm or confirm.strip().lower() != "y":
        return False
    try:
        _registry.record_path(sid).unlink()
    except OSError:
        pass
    return True


def _help_overlay(stdscr) -> None:
    import curses
    lines = [
        "csm watch — keybindings",
        "",
        "  ↑/↓/k/j   move highlight",
        "  PgUp/PgDn page    Home/End first/last",
        "  Enter     focus selected session's window",
        "  r         resume in a new terminal",
        "  n         edit note     p  cycle priority",
        "  s         cycle status  d  mark done",
        "  a         archive       x/Del  DELETE (confirm)",
        "  /         filter",
        "  ?         this help     q/Esc quit",
        "",
        "  Dot: ● live claude proc  ◉ window open  ○ window closed",
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
    # Short tick (200ms) so the focused row's path marquee scrolls smoothly;
    # data refresh still only runs every REFRESH_SECONDS.
    stdscr.timeout(200)

    sel = 0
    top = 0
    filt = ""
    last_refresh = 0.0
    all_rows: list[dict] = []
    rows: list[dict] = []
    force_refresh = True
    marquee_tick = 0
    last_sel = -1
    PROJECT_COL = 36

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

        import csm as _csm
        filt_hint = f"  filter: {filt!r}" if filt else ""
        header = (
            f" csm v{_csm.__version__}  {len(rows)}/{len(all_rows)}{filt_hint}   "
            "↑↓ Enter=focus  r=resume  n=note  p=pri  s=status  d=done  a=archive  x=delete  /=filter  ?=help  q=quit "
        )
        _safe_addnstr(stdscr, 0, 0, header.ljust(w), w, curses.color_pair(2) | curses.A_BOLD)

        col_hdr = (
            f"  {'ID':<8}  {'PRI':<6} {'STATUS':<12} {'TITLE':<44}  "
            f"{_pad('PROJECT (path)', PROJECT_COL)}  WHEN"
        )
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
            # ● = claude process running; ◉ = window open but process not
            # attached; ○ = window not found.
            if r.get("_live"):
                dot = "●"
            elif r.get("_window_open"):
                dot = "◉"
            else:
                dot = "○"
            pri = r.get("priority") or "medium"
            st = r.get("status") or "in_progress"
            sid = (r.get("session_id") or "")[:8]
            title = (r.get("title") or "").replace("\n", " ")
            cwd_path = (r.get("cwd") or r.get("project_name") or "-").replace("\n", " ")
            home = os.path.expanduser("~")
            if cwd_path.startswith(home + "/"):
                cwd_path = "~" + cwd_path[len(home):]
            rel = _relative_time(r.get("last_activity_at"))
            is_sel = idx == sel
            base = curses.color_pair(1) | curses.A_BOLD if is_sel else 0
            # Focused row: marquee-scroll the path. Others: right-fit (last
            # portion visible with leading ellipsis).
            if is_sel:
                proj_str = _marquee(cwd_path, PROJECT_COL, marquee_tick)
            else:
                proj_str = _fit_right(cwd_path, PROJECT_COL)
            line = (
                f"{dot} {_pad(sid, 8)}  {_pad(pri, 6)} {_pad(st, 12)} "
                f"{_pad(title, 44)}  {proj_str}  {_pad(rel, 8)}"
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

        # Marquee tick: reset when selection changes so the focused path
        # always starts from the beginning; advance otherwise.
        if sel != last_sel:
            marquee_tick = 0
            last_sel = sel
        else:
            marquee_tick += 1

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
                sel_row = rows[sel]
                sid = sel_row["session_id"]
                # If the window is clearly closed and the session is still
                # in_progress, auto-resume instead of showing a focus error.
                window_closed = not sel_row.get("_window_open") and not sel_row.get("_live")
                is_in_progress = (sel_row.get("status") or "in_progress") == "in_progress"
                if window_closed and is_in_progress:
                    _run_csm(stdscr, ["resume", sid])
                else:
                    rc = _run_csm(stdscr, ["focus", sid])
                    # Focus failed — fall through to resume if still in_progress.
                    if rc != 0 and is_in_progress:
                        _run_csm(stdscr, ["resume", sid])
                force_refresh = True
            elif rows and k == "r":
                _run_csm(stdscr, ["resume", rows[sel]["session_id"]])
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
            elif rows and k in ("x", "X"):
                if _confirm_delete(stdscr, rows[sel]["session_id"]):
                    if sel > 0:
                        sel -= 1
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
            elif k == curses.KEY_DC and rows:
                if _confirm_delete(stdscr, rows[sel]["session_id"]):
                    if sel > 0:
                        sel -= 1
                    force_refresh = True


def run(refresh_interval: float = REFRESH_SECONDS) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write("csm watch: requires a TTY\n")
        return 2
    import curses
    try:
        curses.wrapper(_tui)
    except KeyboardInterrupt:
        pass
    return 0


# --- csm watch --pin ------------------------------------------------------- #

_PIN_APPLESCRIPT = '''tell application "iTerm2"
    create window with default profile
    tell current session of current window
        write text {cmd}
    end tell
    set bounds of current window to {{100, 100, 900, 600}}
    activate
end tell
'''


def _pin_wezterm() -> int:
    if not shutil.which("wezterm"):
        return 1
    try:
        r = subprocess.run(
            ["wezterm", "cli", "spawn", "--new-window", "--", "csm", "watch"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return 1
    if r.returncode != 0:
        return 1
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'tell application "WezTerm" to activate'], capture_output=True)
    return 0


def _pin_kitty() -> int:
    if not shutil.which("kitty"):
        return 1
    try:
        r = subprocess.run(
            ["kitty", "@", "launch", "--type", "os-window", "csm", "watch"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return 1
    return 0 if r.returncode == 0 else 1


def _pin_iterm() -> int:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return 1
    cmd = '"csm watch"'
    script = _PIN_APPLESCRIPT.format(cmd=cmd)
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return 0 if r.returncode == 0 else 1


def pin_in_iterm() -> int:
    """Open a pinned window running `csm watch` in whichever terminal is available.

    Tries in order: caller's current terminal → WezTerm → Kitty → iTerm2.
    """
    tp = os.environ.get("TERM_PROGRAM") or ""
    if (tp == "WezTerm" or os.environ.get("WEZTERM_PANE")) and _pin_wezterm() == 0:
        return 0
    if (tp == "kitty" or os.environ.get("KITTY_WINDOW_ID")) and _pin_kitty() == 0:
        return 0
    if tp == "iTerm.app" and _pin_iterm() == 0:
        return 0
    for fn in (_pin_wezterm, _pin_kitty, _pin_iterm):
        if fn() == 0:
            return 0
    sys.stderr.write(
        "csm watch --pin: no supported terminal found (install iTerm2, WezTerm, or kitty with remote control)\n"
    )
    return 6
