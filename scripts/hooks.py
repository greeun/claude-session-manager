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

from registry import (
    new_record,
    read as registry_read,
    registry_dir,
    touch_activity,
    upsert_from_hook,
    write as registry_write,
)


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
    window_id: str | None = None
    extra: dict[str, Any] = {}
    # WezTerm.
    if os.environ.get("WEZTERM_PANE"):
        term_app = term_app or "WezTerm"
        window_id = os.environ["WEZTERM_PANE"]
    # Kitty.
    elif os.environ.get("KITTY_WINDOW_ID"):
        term_app = term_app or "kitty"
        window_id = os.environ["KITTY_WINDOW_ID"]
        sock = os.environ.get("KITTY_LISTEN_ON")
        if sock:
            extra["kitty_listen_on"] = sock
    # tmux — a multiplexer that may be inside any outer terminal.
    if os.environ.get("TMUX_PANE"):
        extra["tmux_pane"] = os.environ["TMUX_PANE"]
        tmux_env = os.environ.get("TMUX") or ""
        if tmux_env:
            extra["tmux_socket"] = tmux_env.split(",", 1)[0]
    return {
        "app": term_app,
        "window_id": window_id,
        "tab_id": None,
        "tty": tty,
        **extra,
    }


def _stamp_window_title(short_id: str) -> None:
    """Write an OSC-0 title escape directly to the controlling TTY.

    The hook's stdout is captured by Claude Code, so we must write to
    /dev/tty (the user's actual terminal). Silent on any failure —
    hooks must never block the session.
    """
    title = f"csm:{short_id}"
    esc = f"\x1b]0;{title}\x07"
    try:
        with open("/dev/tty", "w") as fh:
            fh.write(esc)
            fh.flush()
    except OSError:
        pass


def session_start() -> int:
    """Entry: ``csm hook session-start``. Always returns 0."""
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
        _stamp_window_title(sid[:8])
        return 0
    except Exception as e:
        log_error(
            f"session-start: {e.__class__.__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        return 0


_PROMPT_TRUNC_LIMIT = 100
_ELLIPSIS = "\u2026"


def _truncate_prompt(text: str) -> str:
    if not isinstance(text, str):
        return ""
    line = text.splitlines()[0] if text else ""
    if len(line) <= _PROMPT_TRUNC_LIMIT:
        return line
    return line[: _PROMPT_TRUNC_LIMIT - 1] + _ELLIPSIS


def _resolve_prompt(payload: dict[str, Any]) -> str:
    """Extract the user prompt from the UserPromptSubmit payload.

    Reads BOTH ``prompt`` and ``user_prompt`` top-level keys and uses
    the first non-empty string per sprint_contract.md risk §10.1.
    """
    for key in ("prompt", "user_prompt"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def activity() -> int:
    """Entry: ``csm hook activity``. Always returns 0.

    Binding behaviors:
    - Stdin-first payload parsing with env-var fallback (Sprint 1).
    - When payload supplies a ``prompt`` / ``user_prompt`` string,
      writes it into ``last_user_prompt`` (truncated to 100 chars).
    - If the record does not exist yet, creates a skeleton
      (§2.3 option (a)).
    - Always exits 0; no partial writes.
    """
    try:
        payload = _read_stdin_payload()
        sid = _resolve_session_id(payload)
        if not sid:
            log_error("activity: missing session_id (no stdin, no env)")
            return 0

        prompt_text = _resolve_prompt(payload)
        truncated = _truncate_prompt(prompt_text) if prompt_text else ""

        existing = registry_read(sid)
        if existing is None:
            # Create-skeleton branch. touch_activity already creates a
            # minimal record, so we call it then optionally layer in the
            # prompt.
            touch_activity(sid)
            if truncated:
                rec = registry_read(sid)
                if rec is not None:
                    rec["last_user_prompt"] = truncated
                    registry_write(rec)
            return 0

        # In-place update: bump activity, optionally write prompt.
        # µs precision so two prompts in the same wall-clock second are
        # still distinguishable by the scanner's fresher-wins rule.
        from registry import _utc_now_iso as _now_iso
        existing["last_activity_at"] = _now_iso()
        if truncated:
            existing["last_user_prompt"] = truncated
        registry_write(existing)
        return 0
    except Exception as e:
        log_error(
            f"activity: {e.__class__.__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        return 0
