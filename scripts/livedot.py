"""Live-vs-idle dot via ``ps -o pid,tty,comm -A``.

Per sprint_contract.md §2.5. Parses ps output, extracts ttys whose
command basename is exactly ``claude``. Returns a set of
``/dev/<tty>`` paths. Silent degrade on any failure.

Tests monkeypatch ``_run_ps`` to supply canned output.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
from typing import Iterable


def _scanner_log_path():
    from registry import registry_dir
    return registry_dir() / ".scanner-errors.log"


def _log_warning(msg: str) -> None:
    try:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = _scanner_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} livedot: {msg}\n")
    except Exception:
        pass


def _run_ps() -> str:
    """Return ``ps`` stdout as text. Raise on any failure."""
    r = subprocess.run(
        ["ps", "-o", "pid,tty,comm", "-A"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ps exited {r.returncode}: {r.stderr.strip()}")
    return r.stdout


def _normalize_tty(tty: str) -> str | None:
    """Return ``/dev/<tty>`` form, or None for no-tty sentinels."""
    if not tty or tty in ("?", "??", "-"):
        return None
    if tty.startswith("/dev/"):
        return tty
    return f"/dev/{tty}"


def _parse_ps_output(text: str) -> set[str]:
    """Parse ``ps -o pid,tty,comm -A`` output and return live claude ttys.

    Per §2.5:
    - Split each line on whitespace into at most 3 parts.
    - ``pid`` must match ``^[0-9]+$``; skip otherwise (header row etc.).
    - Normalize tty column; skip ``?``/``??``/``-``.
    - basename of the command column must be exactly ``claude``.
    """
    hits: set[str] = set()
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, tty, comm = parts[0], parts[1], parts[2]
        if not pid.isdigit():
            continue
        norm = _normalize_tty(tty)
        if norm is None:
            continue
        if os.path.basename(comm.rstrip()) != "claude":
            continue
        hits.add(norm)
    return hits


def live_ttys() -> set[str]:
    """Return the set of ``/dev/<tty>`` paths that currently host claude.

    Returns an empty set on any failure (logged once).
    """
    try:
        out = _run_ps()
    except (
        subprocess.SubprocessError,
        FileNotFoundError,
        OSError,
        RuntimeError,
    ) as e:
        _log_warning(
            f"ps failed ({e.__class__.__name__}: {e}); "
            f"all rows will render as idle"
        )
        return set()
    try:
        return _parse_ps_output(out)
    except Exception as e:
        _log_warning(f"ps parse failed ({e.__class__.__name__}: {e})")
        return set()


def is_live(record: dict, live_set: Iterable[str]) -> bool:
    """Return True when ``record['terminal']['tty']`` is in ``live_set``."""
    term = record.get("terminal") or {}
    tty = term.get("tty")
    if not tty:
        return False
    return tty in set(live_set)
