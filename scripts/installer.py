"""Installer helpers called from ``install.sh``.

Exit codes:
* 0  — success
* 2  — ``~/.claude/settings.json`` exists but is not valid JSON
       (policy (a): do not modify, do not back up, ask the user to fix)
* 1  — other failures
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

HOOK_COMMANDS = {
    "SessionStart": "csm hook session-start",
    "UserPromptSubmit": "csm hook activity",
}


def _settings_path() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "settings.json"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
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
        try:
            os.unlink(tmpname)
        except FileNotFoundError:
            pass
        raise


def _load_settings(path: Path) -> dict[str, Any]:
    """Load settings.json. On malformed: print + sys.exit(2)."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"csm install: cannot read {path}: {e}\n")
        sys.exit(1)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        sys.stderr.write(
            f"csm install: {path} is not valid JSON ({e.__class__.__name__}: {e}).\n"
            "csm install: refusing to modify it. Please fix or remove the file "
            "and re-run install.\n"
        )
        sys.exit(2)
    if not isinstance(data, dict):
        sys.stderr.write(
            f"csm install: {path} is not a JSON object; refusing to modify.\n"
        )
        sys.exit(2)
    return data


def _existing_commands(hooks_for_event: list[dict[str, Any]]) -> list[str]:
    """Flatten existing ``hooks[<event>][].hooks[].command`` strings."""
    out: list[str] = []
    for matcher in hooks_for_event:
        if not isinstance(matcher, dict):
            continue
        for h in matcher.get("hooks", []):
            if not isinstance(h, dict):
                continue
            cmd = h.get("command")
            if isinstance(cmd, str):
                out.append(cmd)
    return out


_LEGACY_HOOK_PREFIX = "cst hook "


def _strip_legacy_hooks(arr: list[dict[str, Any]]) -> int:
    """Remove our old `cst hook ...` entries in-place. Returns count removed."""
    removed = 0
    i = 0
    while i < len(arr):
        entry = arr[i]
        inner = entry.get("hooks") if isinstance(entry, dict) else None
        if isinstance(inner, list):
            inner[:] = [
                h for h in inner
                if not (
                    isinstance(h, dict)
                    and isinstance(h.get("command"), str)
                    and h["command"].startswith(_LEGACY_HOOK_PREFIX)
                )
            ]
            if not inner:
                arr.pop(i)
                removed += 1
                continue
        i += 1
    return removed


def _merge_hooks(settings: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    """Ensure both hook commands are present (full-string match).

    Returns ``(settings, existing_count, appended_count)``.
    """
    hooks = settings.setdefault("hooks", {})
    existing_total = 0
    appended_total = 0
    for event, command in HOOK_COMMANDS.items():
        arr = hooks.get(event)
        if not isinstance(arr, list):
            arr = []
            hooks[event] = arr
        _strip_legacy_hooks(arr)
        existing_cmds = _existing_commands(arr)
        existing_total += len(existing_cmds)
        # Full-string equality — NOT substring.
        if command in existing_cmds:
            continue
        arr.append(
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": command}],
            }
        )
        appended_total += 1
    return settings, existing_total, appended_total


STATUSLINE_COMMAND = "csm statusline"


def _merge_statusline(settings: dict[str, Any]) -> str:
    """Install our statusLine block iff no competing value is present.

    Returns one of:
    - ``"added"`` — statusLine was absent; we set it to our canonical shape.
    - ``"kept_ours"`` — statusLine already points at ``csm statusline``; no-op.
    - ``"kept_existing"`` — a different statusLine is present; we did NOT overwrite.
    """
    existing = settings.get("statusLine")
    # Treat our legacy `cst statusline` as upgradable (we own it).
    if isinstance(existing, dict):
        ecmd = existing.get("command")
        if isinstance(ecmd, str) and ecmd == "cst statusline":
            existing = None
    if not isinstance(existing, dict):
        settings["statusLine"] = {
            "type": "command",
            "command": STATUSLINE_COMMAND,
            "padding": 0,
        }
        return "added"
    cmd = existing.get("command")
    if isinstance(cmd, str) and cmd == STATUSLINE_COMMAND:
        # Ensure the shape is canonical (idempotent re-install).
        settings["statusLine"] = {
            "type": "command",
            "command": STATUSLINE_COMMAND,
            "padding": 0,
        }
        return "kept_ours"
    return "kept_existing"


def merge_settings() -> int:
    path = _settings_path()
    # Ensure parent directory exists even when we're creating from scratch.
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_settings(path)
    settings, existing_h, appended_h = _merge_hooks(settings)
    status_action = _merge_statusline(settings)
    _atomic_write_json(path, settings)
    sys.stdout.write(
        f"csm install: merged hooks into {path} "
        f"(found {existing_h} existing hook entr{'y' if existing_h == 1 else 'ies'}, "
        f"appended {appended_h} new)\n"
    )
    if status_action == "added":
        sys.stdout.write(
            f"csm install: statusLine set to '{STATUSLINE_COMMAND}'\n"
        )
    elif status_action == "kept_ours":
        sys.stdout.write(
            f"csm install: statusLine already set to '{STATUSLINE_COMMAND}'; no change\n"
        )
    elif status_action == "kept_existing":
        sys.stdout.write(
            "csm install: existing statusline detected; NOT overwriting.\n"
            "csm install: to combine, wrap your current statusLine command so it also calls:\n"
            f"    $(csm statusline)\n"
            "csm install: for example, change your existing command to:\n"
            "    sh -c 'your-existing-command; csm statusline'\n"
        )
    return 0


def ensure_taskdir() -> int:
    p = Path(os.path.expanduser("~")) / ".claude" / "claude-tasks"
    p.mkdir(parents=True, exist_ok=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write("usage: installer.py {merge-settings|ensure-taskdir}\n")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "merge-settings":
        sys.exit(merge_settings())
    if cmd == "ensure-taskdir":
        sys.exit(ensure_taskdir())
    sys.stderr.write(f"installer.py: unknown subcommand: {cmd}\n")
    sys.exit(1)
