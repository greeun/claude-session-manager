"""Per-session JSON registry under ``~/.claude/claude-tasks/``.

One file per session: ``<session_id>.json``. Writes are atomic (tempfile
+ ``os.replace``). A malformed JSON file is renamed to
``<name>.json.corrupt-<unix_ts>`` so the rest of the registry remains
usable and the bad bytes are preserved for inspection.

Tests redirect the registry via the ``CST_REGISTRY_DIR`` env var.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

USER_OWNED_FIELDS = ("title", "priority", "status", "note", "tags")


def _utc_now_iso() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def registry_dir() -> Path:
    """Directory that holds ``<session_id>.json`` files.

    Honors ``CST_REGISTRY_DIR`` (used by tests). Otherwise defaults to
    ``$HOME/.claude/claude-tasks``. Creates the directory on demand.
    """
    override = os.environ.get("CST_REGISTRY_DIR")
    if override:
        p = Path(override)
    else:
        p = Path(os.path.expanduser("~")) / ".claude" / "claude-tasks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_valid_uuid(sid: str) -> bool:
    return bool(_UUID_RE.match(sid or ""))


def short_id(session_id: str) -> str:
    return (session_id or "")[:8]


def record_path(session_id: str) -> Path:
    return registry_dir() / f"{session_id}.json"


def new_record(session_id: str, **overrides: Any) -> dict[str, Any]:
    """Return a fresh auto_detected record with sane defaults."""
    now = _utc_now_iso()
    rec: dict[str, Any] = {
        "session_id": session_id,
        "title": "",
        "priority": "medium",
        "status": "in_progress",
        "cwd": None,
        "project_name": None,
        "tags": [],
        "note": "",
        "created_at": now,
        "last_activity_at": now,
        "terminal": {
            "app": None,
            "window_id": None,
            "tab_id": None,
            "tty": None,
        },
        "auto_detected": True,
        "archived": False,
        "archived_at": None,
    }
    rec.update(overrides)
    return rec


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as JSON to ``path`` atomically via tempfile+replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmpname, path)
    except Exception:
        # Clean up the temp file if replace didn't consume it.
        try:
            os.unlink(tmpname)
        except FileNotFoundError:
            pass
        raise


def _isolate_corrupt(path: Path) -> Path:
    """Rename a corrupt file to ``<name>.corrupt-<unix_ts>`` and return it.

    Byte contents are preserved (rename, never rewrite).
    """
    ts = int(time.time())
    new = path.with_name(f"{path.name}.corrupt-{ts}")
    # If a previous isolate happened this second, append a counter.
    counter = 0
    while new.exists():
        counter += 1
        new = path.with_name(f"{path.name}.corrupt-{ts}-{counter}")
    os.rename(path, new)
    return new


def read(session_id: str) -> dict[str, Any] | None:
    """Return the record for ``session_id`` or ``None`` if missing/corrupt.

    Corrupt files are renamed out of the way before returning ``None``.
    """
    p = record_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            _isolate_corrupt(p)
        except OSError:
            pass
        return None


def write(record: dict[str, Any]) -> Path:
    """Atomically write a complete record; return the path."""
    sid = record["session_id"]
    p = record_path(sid)
    _atomic_write(p, record)
    return p


def update(session_id: str, **fields: Any) -> dict[str, Any] | None:
    """Merge ``fields`` into the existing record and write it back.

    Returns the updated record, or ``None`` if the record does not exist.
    Setting any of the USER_OWNED_FIELDS flips ``auto_detected`` to False.
    """
    rec = read(session_id)
    if rec is None:
        return None
    for k, v in fields.items():
        rec[k] = v
    if any(k in USER_OWNED_FIELDS for k in fields):
        rec["auto_detected"] = False
    write(rec)
    return rec


def upsert_from_hook(
    session_id: str,
    *,
    cwd: str | None = None,
    project_name: str | None = None,
    terminal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a record from hook context.

    Never touches user-owned fields once ``auto_detected`` is False.
    Always bumps ``last_activity_at``.
    """
    existing = read(session_id)
    if existing is None:
        rec = new_record(session_id)
        if cwd is not None:
            rec["cwd"] = cwd
        if project_name is not None:
            rec["project_name"] = project_name
        if terminal is not None:
            # Merge provided non-null terminal fields.
            for k, v in terminal.items():
                if v is not None:
                    rec["terminal"][k] = v
        write(rec)
        return rec

    rec = existing
    rec["last_activity_at"] = _utc_now_iso()
    # Fill only missing context fields, never overwrite.
    if rec.get("cwd") in (None, "") and cwd:
        rec["cwd"] = cwd
    if rec.get("project_name") in (None, "") and project_name:
        rec["project_name"] = project_name
    if terminal:
        for k, v in terminal.items():
            if v is not None and rec["terminal"].get(k) in (None, ""):
                rec["terminal"][k] = v
    write(rec)
    return rec


def touch_activity(session_id: str) -> dict[str, Any] | None:
    """Set ``last_activity_at = now`` on an existing record.

    Creates a minimal record if missing — hooks should always leave a
    trace rather than dropping data.
    """
    rec = read(session_id)
    if rec is None:
        rec = new_record(session_id)
        write(rec)
        return rec
    rec["last_activity_at"] = _utc_now_iso()
    write(rec)
    return rec


def iter_records() -> Iterable[dict[str, Any]]:
    """Yield all valid records in the registry.

    Files that fail to parse are isolated and skipped. Files whose
    filename is not the canonical ``<uuid>.json`` shape are also
    skipped (so ``badfile.json`` gets isolated when it's malformed but
    does not pollute iteration even if it parses as JSON).
    """
    d = registry_dir()
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        if not p.name.endswith(".json"):
            continue
        stem = p.stem  # "<uuid>"
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            try:
                _isolate_corrupt(p)
            except OSError:
                pass
            continue
        # Only treat records with the expected shape as sessions.
        if not isinstance(data, dict) or "session_id" not in data:
            continue
        if not is_valid_uuid(stem):
            # Ignore non-uuid-named JSON files (defensive).
            continue
        yield data


_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def sorted_records(include_archived: bool = False) -> list[dict[str, Any]]:
    """Return records sorted by priority (high→low) then recency desc."""
    recs = list(iter_records())
    if not include_archived:
        recs = [r for r in recs if not r.get("archived")]

    def key(r: dict[str, Any]) -> tuple[int, str]:
        pri = _PRIORITY_ORDER.get(r.get("priority", "medium"), 1)
        # Sort by (priority asc, -last_activity_at) — since timestamps
        # are ISO-8601 Z-suffixed, lexicographic desc == chronological desc.
        return (pri, r.get("last_activity_at") or "")

    # Stable sort: priority ascending then activity descending. Python
    # has no mixed sort, so do it in two passes.
    recs.sort(key=lambda r: r.get("last_activity_at") or "", reverse=True)
    recs.sort(key=lambda r: _PRIORITY_ORDER.get(r.get("priority", "medium"), 1))
    return recs
