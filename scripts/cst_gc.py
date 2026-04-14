"""``cst gc`` — delete records archived ≥ 7 days ago.

Per sprint_contract.md §2.12. Never touches non-archived records.
Uses stored ``archived_at`` timestamp, not file mtime.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Iterable

import registry

SEVEN_DAYS = 604800  # seconds


def _scanner_log_path():
    return registry.registry_dir() / ".scanner-errors.log"


def _log_warning(msg: str) -> None:
    try:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = _scanner_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} gc: {msg}\n")
    except Exception:
        pass


def _parse_iso_z(ts: str) -> _dt.datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    # Accept trailing Z for UTC per our standard format.
    try:
        return _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc
        )
    except ValueError:
        return None


def run() -> int:
    """Execute gc. Returns exit code."""
    deleted = 0
    kept_archived = 0
    errors = 0
    now = _dt.datetime.now(_dt.timezone.utc)

    for rec in list(registry.iter_records()):
        if not rec.get("archived"):
            continue
        archived_at = _parse_iso_z(rec.get("archived_at"))
        if archived_at is None:
            _log_warning(
                f"{rec.get('session_id','?')[:8]}: unparseable archived_at "
                f"{rec.get('archived_at')!r}; skipping"
            )
            kept_archived += 1
            continue
        age = (now - archived_at).total_seconds()
        if age > SEVEN_DAYS:
            path = registry.record_path(rec["session_id"])
            try:
                os.unlink(path)
                deleted += 1
            except OSError as e:
                sys.stderr.write(
                    f"cst gc: failed to delete {path.name}: "
                    f"{e.__class__.__name__}: {e}\n"
                )
                errors += 1
        else:
            kept_archived += 1

    sys.stdout.write(
        f"cst gc: deleted {deleted} record(s); "
        f"kept {kept_archived} archived record(s) still within the 7-day window\n"
    )
    return 1 if errors else 0
