#!/usr/bin/env python3
"""``csm`` — Claude Session Manager CLI.

Sprint 2 subcommands: Sprint 1 plus ``focus``, ``resume``, ``gc``,
``review-stale``, ``statusline``; ``list`` gains ``--compact``,
``--stale``, multi-line rendering, and live-vs-idle dot.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

# Allow running both as `python3 scripts/csm.py ...` and via symlink in
# ~/.local/bin/csm. We add this file's directory to sys.path so the
# sibling modules import cleanly even when the symlink is invoked
# from an arbitrary cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import registry  # noqa: E402
import hooks as hook_mod  # noqa: E402
import scanner  # noqa: E402
import resolver  # noqa: E402
import livedot  # noqa: E402
import statusline as sl_mod  # noqa: E402
import csm_gc as gc_mod  # noqa: E402
import review_stale as rs_mod  # noqa: E402
import focus as focus_mod  # noqa: E402
import resume as resume_mod  # noqa: E402
import platform_macos  # noqa: E402

__version__ = "0.4.1"

ACTIVE_STATUSES = {"in_progress", "blocked", "waiting"}


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #


def _relative_time(iso: str | None) -> str:
    if not iso:
        return "-"
    t = registry.parse_iso_z(iso) if isinstance(iso, str) else None
    if t is None:
        return iso
    delta = _dt.datetime.now(_dt.timezone.utc) - t
    sec = int(delta.total_seconds())
    if sec < 0:
        sec = 0
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def _is_stale(rec: dict, threshold: int, now: _dt.datetime) -> bool:
    if rec.get("archived"):
        return False
    if rec.get("status") not in ACTIVE_STATUSES:
        return False
    t = sl_mod._parse_ts(rec.get("last_activity_at"))
    if t is None:
        return False
    return (now - t).total_seconds() > threshold


def _dot(live: bool) -> str:
    return "\u25cf" if live else "\u25cb"


def _headline(rec: dict, dot: str, display_status: str) -> list[str]:
    return [
        dot,
        registry.short_id(rec.get("session_id", "")),
        rec.get("priority") or "medium",
        display_status,
        rec.get("title") or "",
        rec.get("project_name") or "",
        _relative_time(rec.get("last_activity_at")),
    ]


def _json_for(rec: dict, live: bool) -> dict:
    return {
        "session_id": rec.get("session_id"),
        "short_id": registry.short_id(rec.get("session_id", "")),
        "priority": rec.get("priority"),
        "status": rec.get("status"),
        "title": rec.get("title") or "",
        "project_name": rec.get("project_name") or "",
        "last_activity_at": rec.get("last_activity_at"),
        "archived": bool(rec.get("archived")),
        "last_user_prompt": rec.get("last_user_prompt") or "",
        "last_assistant_summary": rec.get("last_assistant_summary") or "",
        "current_task_hint": rec.get("current_task_hint") or "",
        "live": bool(live),
    }


# --------------------------------------------------------------------------- #
# Session-id resolution
# --------------------------------------------------------------------------- #


def _resolve_id_or_exit(prefix: str) -> str:
    """Thin wrapper around resolver.resolve_or_exit."""
    return resolver.resolve_or_exit(prefix)


def _current_session_id() -> str | None:
    """Best-effort: the Claude Code session active in this shell.

    Strategy:
      1. $CLAUDE_SESSION_ID (set by some Claude Code contexts).
      2. Most-recently-active session whose cwd equals $PWD.
    """
    env_sid = os.environ.get("CLAUDE_SESSION_ID")
    if env_sid:
        return env_sid
    pwd = os.environ.get("PWD") or os.getcwd()
    best = None
    best_ts = ""
    for r in registry.iter_records():
        if (r.get("cwd") or "") != pwd or r.get("archived"):
            continue
        ts = r.get("last_activity_at") or ""
        if ts > best_ts:
            best_ts = ts
            best = r
    return best.get("session_id") if best else None


def cmd_current(args: argparse.Namespace) -> int:
    sid = _current_session_id()
    if not sid:
        sys.stderr.write("csm: no current session found for this cwd\n")
        return 1
    sys.stdout.write(sid + "\n")
    return 0


# --------------------------------------------------------------------------- #
# Subcommand handlers
# --------------------------------------------------------------------------- #


_AUTOSCAN_INTERVAL = 30.0  # seconds

_PRI_STYLE = {"high": "bold red", "medium": "yellow", "low": "dim"}
_STATUS_STYLE = {
    "in_progress": "green",
    "blocked": "red",
    "waiting": "yellow",
    "done": "dim",
    "stale": "bold yellow",
}


def _render_plain_multiline(display_rows: list[dict]) -> None:
    """Plain-text multi-line output — used when stdout is not a TTY."""
    for d in display_rows:
        rec = d["rec"]
        status = "stale" if d["stale"] else (rec.get("status") or "in_progress")
        headline = _headline(rec, _dot(d["live"]), status)
        sys.stdout.write("\t".join(headline) + "\n")
        lup = (rec.get("last_user_prompt") or "").strip()
        if lup:
            sys.stdout.write(f"\t\u2937 {lup}\n")
        cth = (rec.get("current_task_hint") or "").strip()
        if cth:
            sys.stdout.write(f"\t\u2699 {cth}\n")


def _render_pretty(display_rows: list[dict]) -> None:
    """Rich Table rendering for `csm list` (TTY default)."""
    if not sys.stdout.isatty():
        _render_plain_multiline(display_rows)
        return
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        _render_plain_multiline(display_rows)
        return

    console = Console()
    width = console.size.width
    tbl = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False, expand=True)
    tbl.add_column("", width=1, no_wrap=True)
    tbl.add_column("ID", style="green", width=8, no_wrap=True)
    tbl.add_column("Pri", width=6, no_wrap=True)
    tbl.add_column("Status", width=12, no_wrap=True)
    tbl.add_column("Title", ratio=3, no_wrap=True, overflow="ellipsis")
    tbl.add_column("Project", style="blue", width=22, no_wrap=True, overflow="ellipsis")
    tbl.add_column("When", style="dim", width=10, no_wrap=True)

    for d in display_rows:
        rec = d["rec"]
        status = "stale" if d["stale"] else (rec.get("status") or "in_progress")
        pri = rec.get("priority") or "medium"
        sid = (rec.get("session_id") or "")[:8]
        title = (rec.get("title") or "").replace("\n", " ")
        proj = rec.get("project_name") or "-"
        rel = _relative_time(rec.get("last_activity_at"))
        dot = Text("●" if d["live"] else "○", style="green bold" if d["live"] else "dim")
        tbl.add_row(
            dot,
            sid,
            Text(pri, style=_PRI_STYLE.get(pri, "")),
            Text(status, style=_STATUS_STYLE.get(status, "")),
            title,
            proj,
            rel,
        )
        lup = (rec.get("last_user_prompt") or "").strip().replace("\n", " ")
        cth = (rec.get("current_task_hint") or "").strip().replace("\n", " ")
        if lup:
            tbl.add_row("", "", "", "", Text(f"⤷ {lup}", style="dim italic", no_wrap=True, overflow="ellipsis"), "", "")
        if cth:
            tbl.add_row("", "", "", "", Text(f"⚙ {cth}", style="dim cyan", no_wrap=True, overflow="ellipsis"), "", "")

    console.print(tbl)


def _maybe_autoscan() -> None:
    """Run scanner if the last scan is stale. Silent on any failure."""
    marker = registry.registry_dir() / ".last-scan"
    try:
        age = _dt.datetime.now(_dt.timezone.utc).timestamp() - marker.stat().st_mtime
        if age < _AUTOSCAN_INTERVAL:
            return
    except OSError:
        pass
    try:
        scanner.scan_once()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except Exception:
        pass


def cmd_list(args: argparse.Namespace) -> int:
    _maybe_autoscan()
    threshold = sl_mod._stale_threshold_seconds()
    now = _dt.datetime.now(_dt.timezone.utc)
    live_set = livedot.live_ttys()
    sid_map = livedot.live_sid_ttys()

    recs = registry.sorted_records(include_archived=bool(args.all))

    # Attach derived view-state to each record for the display pass.
    display_rows: list[dict] = []
    for r in recs:
        stale = _is_stale(r, threshold, now)
        live = livedot.is_live(r, live_set, sid_map)
        display_rows.append(
            {"rec": r, "stale": stale, "live": live}
        )

    if args.stale:
        display_rows = [d for d in display_rows if d["stale"]]

    if args.json:
        payload = [_json_for(d["rec"], d["live"]) for d in display_rows]
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    if not display_rows:
        sys.stdout.write("(no sessions)\n")
        return 0

    if args.compact:
        for d in display_rows:
            rec = d["rec"]
            display_status = "stale" if d["stale"] else (rec.get("status") or "in_progress")
            headline = _headline(rec, _dot(d["live"]), display_status)
            sys.stdout.write("\t".join(headline) + "\n")
    else:
        _render_pretty(display_rows)

    # Stale banner: count from ALL non-archived rows, NOT the filtered
    # --stale view (else --stale and default would both hide/show it).
    stale_count = sum(
        1 for d in display_rows if d["stale"]
    ) if args.stale else sum(
        1
        for r in recs
        if not r.get("archived") and _is_stale(r, threshold, now)
    )
    if stale_count > 0:
        sys.stdout.write(
            f"\u26a0 {stale_count} stale sessions — run 'csm review-stale'\n"
        )
    if not args.json and sys.stdout.isatty():
        sys.stdout.write(f"\n\033[2mcsm v{__version__}\033[0m\n")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    fields: dict = {}
    if args.title is not None:
        fields["title"] = args.title
    if args.priority is not None:
        if args.priority not in ("high", "medium", "low"):
            sys.stderr.write("csm: --priority must be high|medium|low\n")
            return 1
        fields["priority"] = args.priority
    if args.status is not None:
        if args.status not in ("in_progress", "blocked", "waiting", "done"):
            sys.stderr.write(
                "csm: --status must be in_progress|blocked|waiting|done\n"
            )
            return 1
        fields["status"] = args.status
    if args.note is not None:
        fields["note"] = args.note
    if args.tags is not None:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        fields["tags"] = tags
    if not fields:
        sys.stderr.write("csm: set requires at least one field\n")
        return 1
    rec = registry.update(sid, **fields)
    if rec is None:
        sys.stderr.write(f"csm: record disappeared: {sid}\n")
        return 1
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"csm: record disappeared: {sid}\n")
        return 1
    # Marking done flips auto_detected=False, which locks the scanner
    # out of refreshing the title. If the title is still empty (hook
    # created the record but scan hadn't run yet), seed it from the
    # transcript now so the row isn't blank in `csm watch`.
    if not (rec.get("title") or "").strip():
        transcripts = _find_transcripts(sid)
        if transcripts:
            title_seed, cwd_seed = scanner._seed_from_jsonl(transcripts[0])
            if title_seed:
                rec["title"] = title_seed
            elif rec.get("project_name"):
                rec["title"] = rec["project_name"]
            if cwd_seed and not rec.get("cwd"):
                rec["cwd"] = cwd_seed
    rec["status"] = "done"
    rec["auto_detected"] = False
    registry.write(rec)
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"csm: record disappeared: {sid}\n")
        return 1
    rec["archived"] = True
    rec["archived_at"] = registry._utc_now_iso()
    registry.write(rec)
    return 0


def _find_transcripts(sid: str) -> list[Path]:
    """Locate every JSONL transcript file for `sid` under ~/.claude/projects/."""
    base = scanner.projects_dir()
    if not base.exists():
        return []
    return list(base.glob(f"*/{sid}.jsonl"))


def cmd_delete(args: argparse.Namespace) -> int:
    """Delete a session: registry record + Claude Code transcript(s).

    Destructive. Requires --force or an interactive y/n confirmation.
    Removes both the csm registry record AND the JSONL transcript(s)
    in ~/.claude/projects/. After delete, `csm scan` will NOT re-create
    a draft because the transcript is gone too.

    Use `--keep-transcript` to preserve the JSONL (old behavior).
    """
    sid = _resolve_id_or_exit(args.id)
    rec = registry.read(sid)
    transcripts = _find_transcripts(sid)
    if rec is None and not transcripts:
        sys.stderr.write(f"csm: no such session: {sid}\n")
        return 1
    title = (rec.get("title") if rec else None) or "(untitled)"
    proj = (rec.get("project_name") if rec else None) or "-"
    if not args.force:
        tr_summary = (
            f" + {len(transcripts)} transcript file(s)"
            if transcripts and not args.keep_transcript
            else ""
        )
        sys.stderr.write(
            f"About to delete {sid[:8]}  {title}  [{proj}]\n"
            f"Removing: registry record{tr_summary}.\n"
            f"Type 'y' to confirm: "
        )
        sys.stderr.flush()
        try:
            ans = sys.stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans != "y":
            sys.stderr.write("csm: delete aborted\n")
            return 2
    # Registry file.
    path = registry.record_path(sid)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        sys.stderr.write(f"csm: delete failed on registry file: {e}\n")
        return 1
    # Transcript files.
    removed = 0
    if not args.keep_transcript:
        for t in transcripts:
            try:
                t.unlink()
                removed += 1
            except OSError as e:
                sys.stderr.write(f"csm: warning, could not remove {t}: {e}\n")
    sys.stdout.write(
        f"csm: deleted {sid} (registry + {removed} transcript file(s))\n"
    )
    return 0


def cmd_focus(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    if not platform_macos.is_macos():
        sys.stderr.write(
            f"csm: focus is only supported on macOS "
            f"(detected: {platform_macos.current_platform()})\n"
        )
        return 6
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"csm: record disappeared: {sid}\n")
        return 1
    return focus_mod.run(rec)


def cmd_resume(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    if not platform_macos.is_macos():
        sys.stderr.write(
            f"csm: resume is only supported on macOS "
            f"(detected: {platform_macos.current_platform()})\n"
        )
        return 6
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"csm: record disappeared: {sid}\n")
        return 1
    return resume_mod.run(rec)


def cmd_gc(args: argparse.Namespace) -> int:
    return gc_mod.run()


def cmd_review_stale(args: argparse.Namespace) -> int:
    return rs_mod.run()


def cmd_watch(args: argparse.Namespace) -> int:
    import watch as watch_mod
    if args.pin:
        return watch_mod.pin_in_iterm()
    return watch_mod.run()


def cmd_statusline(args: argparse.Namespace) -> int:
    return sl_mod.run()


def cmd_scan(args: argparse.Namespace) -> int:
    summary = scanner.scan_once()
    sys.stdout.write(
        f"scanned {summary['scanned']}, "
        f"created {summary['created']}, "
        f"updated {summary['updated']}\n"
    )
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    if args.event == "session-start":
        return hook_mod.session_start()
    if args.event == "activity":
        return hook_mod.activity()
    sys.stderr.write(f"csm: unknown hook event: {args.event}\n")
    return 1


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="csm", description="Claude Session Manager")
    p.add_argument("--version", action="version", version=f"csm {__version__}")
    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser("list", help="list sessions")
    pl.add_argument("--all", action="store_true", help="include archived")
    pl.add_argument("--stale", action="store_true", help="only stale")
    pl.add_argument("--json", action="store_true", help="emit JSON array")
    pl.add_argument(
        "--compact",
        action="store_true",
        help="one line per session (no ⤷/⚙ sub-rows)",
    )
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("set", help="set fields on a session")
    ps.add_argument("id")
    ps.add_argument("--title")
    ps.add_argument("--priority")
    ps.add_argument("--status")
    ps.add_argument("--note")
    ps.add_argument("--tags", help="comma-separated")
    ps.set_defaults(func=cmd_set)

    pd = sub.add_parser("done", help="mark session done")
    pd.add_argument("id")
    pd.set_defaults(func=cmd_done)

    pa = sub.add_parser("archive", help="archive session")
    pa.add_argument("id")
    pa.set_defaults(func=cmd_archive)

    pde = sub.add_parser(
        "delete",
        help="permanently delete a session record AND its transcript(s)",
    )
    pde.add_argument("id")
    pde.add_argument("--force", "-f", action="store_true", help="skip y/n prompt")
    pde.add_argument(
        "--keep-transcript",
        action="store_true",
        help="remove registry record only; preserve the JSONL transcript",
    )
    pde.set_defaults(func=cmd_delete)

    psc = sub.add_parser("scan", help="scan ~/.claude/projects")
    psc.set_defaults(func=cmd_scan)

    ph = sub.add_parser("hook", help="hook entry point")
    ph.add_argument("event", choices=["session-start", "activity"])
    ph.set_defaults(func=cmd_hook)

    pf = sub.add_parser("focus", help="bring session's terminal to front (macOS)")
    pf.add_argument("id")
    pf.set_defaults(func=cmd_focus)

    pr = sub.add_parser("resume", help="open new terminal + claude --resume (macOS)")
    pr.add_argument("id")
    pr.set_defaults(func=cmd_resume)

    pgc = sub.add_parser("gc", help="delete records archived > 7 days ago")
    pgc.set_defaults(func=cmd_gc)

    prs = sub.add_parser("review-stale", help="walk through stale sessions interactively")
    prs.set_defaults(func=cmd_review_stale)

    pcu = sub.add_parser("current", help="print current session id (for slash commands)")
    pcu.set_defaults(func=cmd_current)

    psl = sub.add_parser("statusline", help="emit Claude Code statusline text")
    psl.set_defaults(func=cmd_statusline)

    pw = sub.add_parser("watch", help="auto-refreshing TUI over the registry")
    pw.add_argument("--pin", action="store_true", help="open in a pinned iTerm2 window")
    pw.set_defaults(func=cmd_watch)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
