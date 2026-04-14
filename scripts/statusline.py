"""``cst statusline`` — compact pending/stale summary for Claude Code.

Per sprint_contract.md §2.9. Forbidden: subprocess, network, JSONL
parsing. Must return in ≤ 150ms for 200 records.
"""

from __future__ import annotations

import datetime as _dt
import sys

import registry

ACTIVE_STATUSES = {"in_progress", "blocked", "waiting"}


def _stale_threshold_seconds() -> int:
    # Local import to avoid pulling config into Sprint-1 tests that
    # don't need it.
    import config
    return config.stale_threshold_seconds()


def _parse_ts(ts: str) -> _dt.datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc
        )
    except ValueError:
        return None


def counts() -> tuple[int, int]:
    """Return ``(pending, stale)`` counts per §2.9 semantics."""
    pending = 0
    stale = 0
    threshold = _stale_threshold_seconds()
    now = _dt.datetime.now(_dt.timezone.utc)
    for rec in registry.iter_records():
        if rec.get("archived"):
            continue
        status = rec.get("status", "in_progress")
        if status not in ACTIVE_STATUSES:
            continue
        pending += 1
        last_act = _parse_ts(rec.get("last_activity_at"))
        if last_act is None:
            continue
        if (now - last_act).total_seconds() > threshold:
            stale += 1
    return pending, stale


def render(pending: int, stale: int) -> str:
    if pending == 0 and stale == 0:
        return ""
    if stale == 0:
        return f"📋 {pending} pending  →  /tasks"
    return f"📋 {pending} pending · {stale} stale  →  /tasks"


def run() -> int:
    pending, stale = counts()
    line = render(pending, stale)
    sys.stdout.write(line + "\n")
    return 0
