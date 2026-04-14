"""User config loader for ``~/.claude/claude-tasks.config.json``.

Per sprint_contract.md §2.10. Only one key is honored this sprint:
``stale_threshold_seconds`` (int > 0).

Precedence: ``CST_STALE_THRESHOLD_SECONDS`` env var > config file >
default (14400s, i.e. 4h).

All invalid values silently fall back to the default and append one
warning line to ``~/.claude/claude-tasks/.scanner-errors.log`` naming
the offending key.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_STALE_THRESHOLD_SECONDS = 14400  # 4 hours


def _log_path() -> Path:
    # Import locally to honor CST_REGISTRY_DIR overrides set by tests.
    from registry import registry_dir
    return registry_dir() / ".scanner-errors.log"


def _log_warning(msg: str) -> None:
    try:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} config: {msg}\n")
    except Exception:
        pass


def config_path() -> Path:
    override = os.environ.get("CST_CONFIG_PATH")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".claude" / "claude-tasks.config.json"


def _load_config_dict() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        _log_warning(
            f"claude-tasks.config.json unreadable ({e.__class__.__name__}); "
            f"stale_threshold_seconds falls back to default"
        )
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        _log_warning(
            f"claude-tasks.config.json is not valid JSON "
            f"({e.__class__.__name__}: {e}); "
            f"stale_threshold_seconds falls back to default"
        )
        return {}
    if not isinstance(data, dict):
        _log_warning(
            "claude-tasks.config.json top level is not an object; "
            "stale_threshold_seconds falls back to default"
        )
        return {}
    return data


def stale_threshold_seconds() -> int:
    """Resolve the effective stale threshold per §2.10 precedence.

    Env var wins, then config file, then default.
    """
    env = os.environ.get("CST_STALE_THRESHOLD_SECONDS")
    if env is not None:
        try:
            v = int(env)
            if v > 0:
                return v
        except ValueError:
            pass
        # Fall through to config/default.

    data = _load_config_dict()
    if "stale_threshold_seconds" not in data:
        return DEFAULT_STALE_THRESHOLD_SECONDS
    v = data["stale_threshold_seconds"]
    # Reject bool explicitly (isinstance(True, int) is True in Python).
    if type(v) is not int:
        _log_warning(
            f"claude-tasks.config.json: stale_threshold_seconds has type "
            f"{type(v).__name__}; must be a positive int. Falling back to default."
        )
        return DEFAULT_STALE_THRESHOLD_SECONDS
    if v <= 0:
        _log_warning(
            f"claude-tasks.config.json: stale_threshold_seconds={v} is not "
            f"positive; must be > 0. Falling back to default."
        )
        return DEFAULT_STALE_THRESHOLD_SECONDS
    return v
