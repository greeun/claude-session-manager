"""``csm review-stale`` — interactive keep/done/archive/skip wizard.

Per sprint_contract.md §2.6. keep/skip are byte-identical no-ops.
done/archive mutate. Non-interactive mode treats remaining as skip.
"""

from __future__ import annotations

import datetime as _dt
import sys

import registry
import statusline as sl_mod

_MAX_REPROMPTS = 3


def _relative(ts: str) -> str:
    t = sl_mod._parse_ts(ts)
    if t is None:
        return "?"
    delta = _dt.datetime.now(_dt.timezone.utc) - t
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _is_stale(rec: dict, threshold: int, now: _dt.datetime) -> bool:
    if rec.get("archived"):
        return False
    if rec.get("status") not in sl_mod.ACTIVE_STATUSES:
        return False
    t = sl_mod._parse_ts(rec.get("last_activity_at"))
    if t is None:
        return False
    return (now - t).total_seconds() > threshold


def _stale_records() -> list[dict]:
    threshold = sl_mod._stale_threshold_seconds()
    now = _dt.datetime.now(_dt.timezone.utc)
    return [r for r in registry.sorted_records() if _is_stale(r, threshold, now)]


def _prompt_one(rec: dict, idx: int, total: int, *, inp=None, out=None) -> str:
    inp = inp or sys.stdin
    out = out or sys.stdout
    sid = rec.get("session_id", "")
    short = sid[:8]
    title = rec.get("title") or ""
    proj = rec.get("project_name") or ""
    rel = _relative(rec.get("last_activity_at") or "")
    out.write(f"[{idx}/{total}] {short} {title} ({proj}) — idle {rel}\n")
    for _ in range(_MAX_REPROMPTS):
        out.write("keep/done/archive/skip [k/d/a/s]: ")
        out.flush()
        line = inp.readline()
        if not line:  # EOF → non-interactive skip
            return "skip"
        tok = line.strip().lower()
        if tok in ("k", "keep"):
            return "keep"
        if tok in ("d", "done"):
            return "done"
        if tok in ("a", "archive"):
            return "archive"
        if tok in ("s", "skip"):
            return "skip"
        out.write("please answer k, d, a, or s.\n")
    # Too many wrong answers: skip defensively.
    return "skip"


def _apply(rec: dict, choice: str) -> None:
    sid = rec["session_id"]
    if choice in ("keep", "skip"):
        return  # byte-identical no-op
    if choice == "done":
        rec["status"] = "done"
        rec["auto_detected"] = False
        registry.write(rec)
        return
    if choice == "archive":
        rec["archived"] = True
        rec["archived_at"] = _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        registry.write(rec)
        return


def run(*, inp=None, out=None) -> int:
    out = out or sys.stdout
    stale = _stale_records()
    if not stale:
        out.write("csm review-stale: no stale sessions.\n")
        return 0
    total = len(stale)
    for i, rec in enumerate(stale, start=1):
        # Re-read to ensure we act on the latest on-disk state.
        fresh = registry.read(rec["session_id"]) or rec
        choice = _prompt_one(fresh, i, total, inp=inp, out=out)
        _apply(fresh, choice)
    return 0
