#!/usr/bin/env python3
"""``cst`` — Claude Session Manager CLI.

Sprint 1 subcommands: ``list``, ``set``, ``done``, ``archive``, ``scan``,
``hook {session-start|activity}``, plus ``--version``.
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
# sibling modules (registry, scanner, hooks) import cleanly even when
# the symlink is invoked from an arbitrary cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import registry  # noqa: E402
import hooks as hook_mod  # noqa: E402
import scanner  # noqa: E402

__version__ = "0.1.0"


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


def _row_for(rec: dict) -> list[str]:
    return [
        registry.short_id(rec.get("session_id", "")),
        rec.get("priority") or "medium",
        rec.get("status") or "in_progress",
        rec.get("title") or "",
        rec.get("project_name") or "",
        _relative_time(rec.get("last_activity_at")),
    ]


def _json_for(rec: dict) -> dict:
    return {
        "session_id": rec.get("session_id"),
        "short_id": registry.short_id(rec.get("session_id", "")),
        "priority": rec.get("priority"),
        "status": rec.get("status"),
        "title": rec.get("title") or "",
        "project_name": rec.get("project_name") or "",
        "last_activity_at": rec.get("last_activity_at"),
        "archived": bool(rec.get("archived")),
    }


# --------------------------------------------------------------------------- #
# Session-id lookup
# --------------------------------------------------------------------------- #


def _resolve_id_exact(sid: str) -> str | None:
    """Sprint 1: require full UUID match. Returns the id or None."""
    if not sid:
        return None
    p = registry.record_path(sid)
    if p.exists():
        return sid
    return None


# --------------------------------------------------------------------------- #
# Subcommand handlers
# --------------------------------------------------------------------------- #


def cmd_list(args: argparse.Namespace) -> int:
    recs = registry.sorted_records(include_archived=bool(args.all))
    if args.stale:
        # stale flag is a forward-compat filter; Sprint 1 does not mark
        # anything stale, so this simply returns nothing for now.
        recs = [r for r in recs if r.get("status") == "stale"]

    if args.json:
        payload = [_json_for(r) for r in recs]
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    if not recs:
        sys.stdout.write("(no sessions)\n")
        return 0

    for r in recs:
        sys.stdout.write("\t".join(_row_for(r)) + "\n")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    sid = _resolve_id_exact(args.id)
    if sid is None:
        sys.stderr.write(f"cst: no such session: {args.id}\n")
        return 1
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
    sid = _resolve_id_exact(args.id)
    if sid is None:
        sys.stderr.write(f"cst: no such session: {args.id}\n")
        return 1
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"cst: record disappeared: {sid}\n")
        return 1
    rec["status"] = "done"
    rec["auto_detected"] = False
    registry.write(rec)
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    sid = _resolve_id_exact(args.id)
    if sid is None:
        sys.stderr.write(f"cst: no such session: {args.id}\n")
        return 1
    rec = registry.read(sid)
    if rec is None:
        sys.stderr.write(f"cst: record disappeared: {sid}\n")
        return 1
    rec["archived"] = True
    rec["archived_at"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    registry.write(rec)
    return 0


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
