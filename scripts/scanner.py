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

_TAIL_WINDOW_LINES = 50
_TRUNC_LIMIT = 100  # Including the trailing "…" when truncated.
_ELLIPSIS = "\u2026"

# Tool categorisation for current_task_hint.
_TOOL_BASH = {"Bash"}
_TOOL_EDIT = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_TOOL_READ = {"Read"}
_TOOL_SEARCH = {"Grep", "Glob"}


def _scanner_log_path() -> Path:
    from registry import registry_dir
    return registry_dir() / ".scanner-errors.log"


def _log_scanner_error(msg: str) -> None:
    try:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = _scanner_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} scanner: {msg}\n")
    except Exception:
        pass


def _truncate(text: str) -> str:
    """Truncate on code-point boundaries; append single '…' when shortened.

    The result is guaranteed ≤ ``_TRUNC_LIMIT`` code points.
    """
    if text is None:
        return ""
    # First line only.
    line = text.splitlines()[0] if text else ""
    if len(line) <= _TRUNC_LIMIT:
        return line
    # Reserve one char for the ellipsis.
    return line[: _TRUNC_LIMIT - 1] + _ELLIPSIS


def _extract_assistant_text(content: Any) -> str:
    """Join text parts of an assistant message; skip tool_use parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if not isinstance(p, dict):
                if isinstance(p, str):
                    parts.append(p)
                continue
            if p.get("type") == "text":
                t = p.get("text")
                if isinstance(t, str):
                    parts.append(t)
            # tool_use parts are intentionally skipped.
        return "".join(parts)
    return ""


def _last_tool_use(content: Any) -> dict | None:
    """Return the last ``tool_use`` part within a content array, or None."""
    if not isinstance(content, list):
        return None
    tu: dict | None = None
    for p in content:
        if isinstance(p, dict) and p.get("type") == "tool_use":
            tu = p
    return tu


def _build_task_hint(tu: dict, cwd: str | None) -> str:
    """Return the truncated hint string for a tool_use part, or '' to skip."""
    name = tu.get("name")
    if not isinstance(name, str) or not name:
        return ""
    inp = tu.get("input") if isinstance(tu.get("input"), dict) else {}

    def _path_label(fp: str) -> str:
        if cwd and isinstance(cwd, str):
            try:
                if fp.startswith(cwd.rstrip("/") + "/"):
                    rel = os.path.relpath(fp, cwd)
                    return rel
            except ValueError:
                pass
        return os.path.basename(fp)

    hint: str
    if name in _TOOL_BASH:
        cmd = inp.get("command") if isinstance(inp, dict) else None
        if isinstance(cmd, str) and cmd.strip():
            hint = f"Running: {cmd.strip()}"
        else:
            hint = name
    elif name in _TOOL_EDIT:
        fp = inp.get("file_path") if isinstance(inp, dict) else None
        if isinstance(fp, str) and fp.strip():
            hint = f"Editing: {_path_label(fp)}"
        else:
            hint = name
    elif name in _TOOL_READ:
        fp = inp.get("file_path") if isinstance(inp, dict) else None
        if isinstance(fp, str) and fp.strip():
            hint = f"Reading: {_path_label(fp)}"
        else:
            hint = name
    elif name in _TOOL_SEARCH:
        pat = inp.get("pattern") if isinstance(inp, dict) else None
        if isinstance(pat, str) and pat.strip():
            hint = f"Searching: {pat.strip()}"
        else:
            hint = name
    else:
        # Unrecognised tool: bare name.
        hint = name
    return _truncate(hint)


def _tail_jsonl(path: Path, n: int) -> list[dict]:
    """Return the last ``n`` decodable JSON-object lines from ``path``.

    Non-UTF-8 bytes are replaced (``errors='replace'``). Malformed lines
    are silently dropped. The window contains EXACTLY the last ``n``
    non-empty lines when the file has ≥ n of them, else all of them.
    """
    buf: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            s = raw.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            buf.append(obj)
            if len(buf) > n:
                buf.pop(0)
    return buf


def _extract_progress(
    jsonl_path: Path, cwd: str | None
) -> tuple[str, str, str] | None:
    """Return (last_user_prompt, last_assistant_summary, current_task_hint).

    Returns ``None`` on any unrecoverable error; callers must then leave
    existing progress fields unchanged and write a single warning.
    """
    try:
        tail = _tail_jsonl(jsonl_path, _TAIL_WINDOW_LINES)
    except OSError as e:
        _log_scanner_error(
            f"{jsonl_path.name}: read failed ({e.__class__.__name__}: {e})"
        )
        return None

    last_user_prompt = ""
    last_assistant_summary = ""
    current_task_hint = ""

    try:
        # Most recent user line in the tail.
        for row in reversed(tail):
            if row.get("type") != "user":
                continue
            msg = row.get("message")
            content = msg.get("content") if isinstance(msg, dict) else row.get("content")
            text = _extract_text(content) or ""
            if text:
                last_user_prompt = _truncate(text)
                break

        # Most recent assistant line in the tail — for the summary only
        # join text parts, skip tool_use parts.
        for row in reversed(tail):
            if row.get("type") != "assistant":
                continue
            msg = row.get("message")
            content = msg.get("content") if isinstance(msg, dict) else row.get("content")
            text = _extract_assistant_text(content).strip()
            if text:
                last_assistant_summary = _truncate(text)
                break

        # Most recent tool_use part anywhere in the tail.
        for row in reversed(tail):
            msg = row.get("message")
            content = msg.get("content") if isinstance(msg, dict) else row.get("content")
            tu = _last_tool_use(content)
            if tu is not None:
                current_task_hint = _build_task_hint(tu, cwd)
                break
    except Exception as e:
        _log_scanner_error(
            f"{jsonl_path.name}: progress extraction failed "
            f"({e.__class__.__name__}: {e})"
        )
        return None

    return last_user_prompt, last_assistant_summary, current_task_hint


def projects_dir() -> Path:
    override = os.environ.get("CST_PROJECTS_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def decode_project_slug(slug: str) -> str:
    """Decode a Claude Code project slug to a human ``project_name``.

    Claude Code encodes the absolute project path by replacing every
    ``/`` with ``-`` (including the leading root slash). This encoding
    is lossy because a literal ``-`` in the original directory name is
    indistinguishable from a path separator.

    Heuristic contract (binding for Sprint 1, see sprint_contract.md §2
    and risk §10.8): strip exactly one leading ``-``, then split on
    ``-``. If the resulting list has ≤ 3 parts, take the last two parts
    joined with ``-``; otherwise take the last part. Empty input is
    returned unchanged.

    Examples:
      * ``-tmp-fake-proj`` → 3 parts (``tmp``,``fake``,``proj``) → ``fake-proj``
      * ``-Users-alice-proj-foo`` → 4 parts → ``foo``
      * ``plainfoo`` → ``plainfoo``
    """
    if not slug:
        return slug
    s = slug[1:] if slug.startswith("-") else slug
    if not s:
        return slug
    parts = s.split("-")
    if len(parts) <= 1:
        return parts[0] or slug
    if len(parts) <= 3:
        return "-".join(parts[-2:])
    return parts[-1]


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
        with path.open("r", encoding="utf-8", errors="replace") as fh:
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
    """Transcript file mtime as a µs-precision UTC ``...Z`` string.

    Float precision matches ``os.stat().st_mtime`` (nanoseconds on
    APFS, rounded to microseconds by ``strftime("%f")``). This is
    what the "fresher wins" comparison in §2.2 compares against
    ``last_activity_at``.
    """
    try:
        ts = path.stat().st_mtime
    except OSError:
        ts = _dt.datetime.now(_dt.timezone.utc).timestamp()
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


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
                # Fresh record has no prior progress; extract and store.
                prog = _extract_progress(jf, cwd_seed)
                if prog is not None:
                    lup, las, cth = prog
                    rec["last_user_prompt"] = lup
                    rec["last_assistant_summary"] = las
                    rec["current_task_hint"] = cth
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

            # Progress extraction. Apply "fresher wins" rule per §2.2
            # option (b): scanner only overwrites ``last_user_prompt``
            # when the JSONL mtime is strictly newer than the record's
            # ``last_activity_at`` AND the extracted value is non-empty
            # AND differs from the stored value. The other two fields
            # (assistant summary, task hint) are scanner-only and
            # always refreshed when non-empty.
            #
            # Binding: timestamps are compared as parsed datetimes, not
            # strings. Mixed-precision compare (seconds-only legacy
            # stored value vs µs-precision mtime) is handled
            # correctly by parsing both through ``parse_iso_z``.
            from registry import parse_iso_z as _piz
            prog = _extract_progress(jf, existing.get("cwd") or cwd_seed)
            if prog is not None:
                lup_new, las_new, cth_new = prog
                stored_last_act_dt = _piz(existing.get("last_activity_at") or "")
                mtime_dt = _piz(mtime)
                jsonl_newer = (
                    stored_last_act_dt is None
                    or (mtime_dt is not None and mtime_dt > stored_last_act_dt)
                )
                cur_lup = existing.get("last_user_prompt", "")
                if (
                    lup_new
                    and lup_new != cur_lup
                    and jsonl_newer
                ):
                    existing["last_user_prompt"] = lup_new
                    changed = True
                # Assistant summary + task hint are scanner-only; refresh
                # whenever the extracted value differs from stored.
                if existing.get("last_assistant_summary", "") != las_new:
                    existing["last_assistant_summary"] = las_new
                    changed = True
                if existing.get("current_task_hint", "") != cth_new:
                    existing["current_task_hint"] = cth_new
                    changed = True

            # Always refresh last_activity_at to reflect file mtime, but
            # only if newer than the stored value (so that more recent
            # hook activity wins). Compared as datetimes, not strings,
            # for correct mixed-precision handling.
            stored_la = _piz(existing.get("last_activity_at") or "")
            mtime_la = _piz(mtime)
            if mtime_la is not None and (stored_la is None or mtime_la > stored_la):
                existing["last_activity_at"] = mtime
                changed = True

            if changed:
                registry_write(existing)
                updated += 1

    return {"scanned": scanned, "created": created, "updated": updated}
