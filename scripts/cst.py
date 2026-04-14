#!/usr/bin/env python3
"""``cst`` — Claude Session Manager CLI.

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

# Allow running both as `python3 scripts/cst.py ...` and via symlink in
# ~/.local/bin/cst. We add this file's directory to sys.path so the
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
import cst_gc as gc_mod  # noqa: E402
import review_stale as rs_mod  # noqa: E402
import focus as focus_mod  # noqa: E402
import resume as resume_mod  # noqa: E402
import platform_macos  # noqa: E402

__version__ = "0.2.0"

ACTIVE_STATUSES = {"in_progress", "blocked", "waiting"}


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #


def _relative_time(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        t = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return iso
    delta = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - t
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


# --------------------------------------------------------------------------- #
# Subcommand handlers
# --------------------------------------------------------------------------- #


def cmd_list(args: argparse.Namespace) -> int:
    threshold = sl_mod._stale_threshold_seconds()
    now = _dt.datetime.now(_dt.timezone.utc)
    live_set = livedot.live_ttys()

    recs = registry.sorted_records(include_archived=bool(args.all))

    # Attach derived view-state to each record for the display pass.
    display_rows: list[dict] = []
    for r in recs:
        stale = _is_stale(r, threshold, now)
        live = livedot.is_live(r, live_set)
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

    for d in display_rows:
        rec = d["rec"]
        display_status = "stale" if d["stale"] else (rec.get("status") or "in_progress")
        headline = _headline(rec, _dot(d["live"]), display_status)
        sys.stdout.write("\t".join(headline) + "\n")
        if args.compact:
            continue
        lup = rec.get("last_user_prompt") or ""
        if lup:
            sys.stdout.write(f"\t\u2937 {lup}\n")
        cth = rec.get("current_task_hint") or ""
        if cth:
            sys.stdout.write(f"\t\u2699 {cth}\n")

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
            f"\u26a0 {stale_count} stale sessions — run 'cst review-stale'\n"
        )
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    fields: dict = {}
    if args.title is not None:
        fields["title"] = args.title
    if args.priority is not None:
        if args.priority not in ("high", "medium", "low"):
            sys.stderr.write("cst: --priority must be high|medium|low\n")
            return 1
        fields["priority"] = args.priority
    if args.status is not None:
        if args.status not in ("in_progress", "blocked", "waiting", "done"):
            sys.stderr.write(
                "cst: --status must be in_progress|blocked|waiting|done\n"
            )
            return 1
        fields["status"] = args.status
    if args.note is not None:
        fields["note"] = args.note
    if args.tags is not None:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        fields["tags"] = tags
    if not fields:
        sys.stderr.write("cst: set requires at least one field\n")
        return 1
    rec = registry.update(sid, **fields)
    if rec is None:
        sys.stderr.write(f"cst: record disappeared: {sid}\n")
        return 1
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"cst: record disappeared: {sid}\n")
        return 1
    rec["status"] = "done"
    rec["auto_detected"] = False
    registry.write(rec)
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"cst: record disappeared: {sid}\n")
        return 1
    rec["archived"] = True
    rec["archived_at"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    registry.write(rec)
    return 0


def cmd_focus(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    if not platform_macos.is_macos():
        sys.stderr.write(
            f"cst: focus is only supported on macOS "
            f"(detected: {platform_macos.current_platform()})\n"
        )
        return 6
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"cst: record disappeared: {sid}\n")
        return 1
    return focus_mod.run(rec)


def cmd_resume(args: argparse.Namespace) -> int:
    sid = _resolve_id_or_exit(args.id)
    if not platform_macos.is_macos():
        sys.stderr.write(
            f"cst: resume is only supported on macOS "
            f"(detected: {platform_macos.current_platform()})\n"
        )
        return 6
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"cst: record disappeared: {sid}\n")
        return 1
    return resume_mod.run(rec)


def cmd_gc(args: argparse.Namespace) -> int:
    return gc_mod.run()


def cmd_review_stale(args: argparse.Namespace) -> int:
    return rs_mod.run()


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
    sys.stderr.write(f"cst: unknown hook event: {args.event}\n")
    return 1


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cst", description="Claude Session Manager")
    p.add_argument("--version", action="version", version=f"cst {__version__}")
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

    psl = sub.add_parser("statusline", help="emit Claude Code statusline text")
    psl.set_defaults(func=cmd_statusline)

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
