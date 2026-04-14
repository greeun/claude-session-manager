"""Hook entry points: ``session-start`` and ``activity``.

Contract (binding, see sprint_contract.md §2):

* Stdin JSON payload is parsed FIRST. Env vars are fallback only.
* When both are present and disagree, stdin wins.
* Any failure is logged to ``~/.claude/claude-tasks/.hook-errors.log``
  with a UTC ISO-8601 timestamp. The hook always exits 0.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from registry import registry_dir, upsert_from_hook, touch_activity


def _log_path() -> Path:
    return registry_dir() / ".hook-errors.log"


def log_error(msg: str) -> None:
    """Append a timestamped error line. Never raises."""
    try:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{ts} {msg}\n"
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        # Never raise from the logger itself.
        pass


def _read_stdin_payload() -> dict[str, Any]:
    """Parse JSON from stdin if non-empty and not a TTY. Else ``{}``."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
    except (ValueError, OSError):
        # Closed stdin, etc. — treat as empty.
        return {}
    try:
        data = sys.stdin.read()
    except Exception:
        return {}
    if not data or not data.strip():
        return {}
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        log_error(f"hook stdin not valid JSON: {e.__class__.__name__}: {e}")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _resolve_session_id(payload: dict[str, Any]) -> str | None:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid:
        return sid
    env_sid = os.environ.get("CLAUDE_SESSION_ID")
    if env_sid:
        return env_sid
    return None


def _resolve_cwd(payload: dict[str, Any]) -> str | None:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    for key in ("CLAUDE_PROJECT_DIR", "PWD"):
        v = os.environ.get(key)
        if v:
            return v
    return None


def _terminal_capture() -> dict[str, Any]:
    term_app = os.environ.get("TERM_PROGRAM") or None
    tty: str | None = None
    try:
        tty = os.ttyname(0)
    except OSError:
        pass
    return {
        "app": term_app,
        "window_id": None,
        "tab_id": None,
        "tty": tty,
    }


def session_start() -> int:
    """Entry: ``cst hook session-start``. Always returns 0."""
    try:
        payload = _read_stdin_payload()
        sid = _resolve_session_id(payload)
        if not sid:
            log_error("session-start: missing session_id (no stdin, no env)")
            return 0
        cwd = _resolve_cwd(payload)
        project_name = None
        if cwd:
            project_name = os.path.basename(cwd.rstrip("/")) or None
        upsert_from_hook(
            sid,
            cwd=cwd,
            project_name=project_name,
            terminal=_terminal_capture(),
        )
        return 0
    except Exception as e:
        log_error(
            f"session-start: {e.__class__.__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        return 0


def activity() -> int:
    """Entry: ``cst hook activity``. Always returns 0."""
    try:
        payload = _read_stdin_payload()
        sid = _resolve_session_id(payload)
        if not sid:
            log_error("activity: missing session_id (no stdin, no env)")
            return 0
        touch_activity(sid)
        return 0
    except Exception as e:
        log_error(
            f"activity: {e.__class__.__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        return 0
