"""Scan ``~/.claude/projects/`` and upsert per-session registry records.

Contract (binding, see sprint_contract.md §2):

* Filename stem must match the canonical UUID regex; non-matching files
  are silently skipped.
* Title seeding: first ``type == "user"`` line yielding extractable
  plain text wins; fall back to ``project_name``.
* Project-slug decode: strip one leading ``-``, replace remaining ``-``
  with ``/``, take basename. Empty basename → raw slug.
* ``cwd`` is captured from the first JSONL line's ``cwd`` field if
  present, else ``None``.
* Never overwrites user-owned fields once ``auto_detected`` is False.
* Never archives and never deletes.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

from registry import (
    USER_OWNED_FIELDS,
    is_valid_uuid,
    new_record,
    read as registry_read,
    write as registry_write,
)


def projects_dir() -> Path:
    override = os.environ.get("CST_PROJECTS_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def decode_project_slug(slug: str) -> str:
    """Decode a slug like ``-tmp-fake-proj`` → ``fake-proj``.

    Rule: strip exactly one leading ``-``, replace remaining ``-`` with
    ``/``, take basename. If the basename is empty, return the raw slug.
    """
    if not slug:
        return slug
    s = slug[1:] if slug.startswith("-") else slug
    path = s.replace("-", "/")
    base = os.path.basename(path)
    return base if base else slug


def _extract_text(msg_content: Any) -> str | None:
    """Best-effort extraction of plain text from a message ``content``."""
    if isinstance(msg_content, str):
        return msg_content
    if isinstance(msg_content, list):
        parts: list[str] = []
        for part in msg_content:
            if isinstance(part, dict):
                # Common shapes: {"type":"text","text":"..."}
                t = part.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(part, str):
                parts.append(part)
        joined = "".join(parts).strip()
        return joined or None
    return None


def _seed_from_jsonl(path: Path) -> tuple[str | None, str | None]:
    """Return ``(title_seed, cwd_seed)`` from a transcript file.

    ``title_seed`` is the first user message's text trimmed to 60 chars,
    or ``None`` if none available. ``cwd_seed`` is the first line's
    ``cwd`` field if present.
    """
    title_seed: str | None = None
    cwd_seed: str | None = None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if cwd_seed is None:
                    c = row.get("cwd")
                    if isinstance(c, str) and c:
                        cwd_seed = c
                if title_seed is None and row.get("type") == "user":
                    msg = row.get("message")
                    if isinstance(msg, dict):
                        txt = _extract_text(msg.get("content"))
                    else:
                        txt = _extract_text(row.get("content"))
                    if txt:
                        title_seed = txt.strip().splitlines()[0][:60]
                if title_seed is not None and cwd_seed is not None:
                    break
    except OSError:
        pass
    return title_seed, cwd_seed


def _mtime_iso(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        ts = _dt.datetime.utcnow().timestamp()
    return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_once() -> dict[str, int]:
    """Walk the projects tree; upsert draft records.

    Returns summary counters: ``{'scanned': N, 'created': M, 'updated': K}``.
    """
    scanned = 0
    created = 0
    updated = 0

    base = projects_dir()
    if not base.is_dir():
        return {"scanned": 0, "created": 0, "updated": 0}

    for project_entry in sorted(base.iterdir()):
        if not project_entry.is_dir():
            continue
        slug = project_entry.name
        project_name = decode_project_slug(slug)
        for jf in sorted(project_entry.glob("*.jsonl")):
            stem = jf.stem
            if not is_valid_uuid(stem):
                # Non-uuid filenames are silently skipped.
                continue
            scanned += 1
            title_seed, cwd_seed = _seed_from_jsonl(jf)
            mtime = _mtime_iso(jf)

            existing = registry_read(stem)
            if existing is None:
                rec = new_record(stem)
                rec["cwd"] = cwd_seed
                rec["project_name"] = project_name
                rec["last_activity_at"] = mtime
                rec["title"] = title_seed or (project_name or "")
                registry_write(rec)
                created += 1
                continue

            changed = False
            # Never overwrite archived records' flags.
            # Never overwrite user-owned fields once auto_detected is False.
            is_user_owned = not existing.get("auto_detected", True)

            if not is_user_owned:
                # Safe to refresh auto-seeded title/project_name.
                new_title = title_seed or project_name or existing.get("title")
                if new_title and existing.get("title") != new_title:
                    existing["title"] = new_title
                    changed = True
                if project_name and existing.get("project_name") != project_name:
                    existing["project_name"] = project_name
                    changed = True

            # cwd is a context field (not user-owned) but we only fill when empty.
            if (existing.get("cwd") in (None, "")) and cwd_seed:
                existing["cwd"] = cwd_seed
                changed = True

            # Always refresh last_activity_at to reflect file mtime, but
            # only if newer than the stored value (so that more recent
            # hook activity wins).
            if mtime > (existing.get("last_activity_at") or ""):
                existing["last_activity_at"] = mtime
                changed = True

            if changed:
                registry_write(existing)
                updated += 1

    return {"scanned": scanned, "created": created, "updated": updated}
