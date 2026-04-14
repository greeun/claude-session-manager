"""Short-id prefix resolver for every CLI subcommand that takes an id.

Per sprint_contract.md §2.7. Exit codes pinned:
- 1 = not found
- 2 = prefix too short (< 6 hex chars)
- 3 = ambiguous (multiple candidates)
"""

from __future__ import annotations

import re
import sys
from typing import NamedTuple

import registry

_HEX6 = re.compile(r"^[0-9a-f]{6,}$")
_UUID_RE = registry._UUID_RE  # reuse

TOO_SHORT = "TOO_SHORT"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS"
OK = "OK"


class Resolution(NamedTuple):
    status: str
    session_id: str | None
    candidates: list[dict]


def resolve(prefix: str) -> Resolution:
    """Resolve ``prefix`` to a session_id per §2.7 rules."""
    if not prefix:
        return Resolution(TOO_SHORT, None, [])
    # Exact full-UUID shortcut.
    if _UUID_RE.match(prefix):
        p = registry.record_path(prefix)
        if p.exists():
            return Resolution(OK, prefix, [])
        return Resolution(NOT_FOUND, None, [])
    # Prefix form.
    if not _HEX6.match(prefix):
        return Resolution(TOO_SHORT, None, [])
    # Scan registry for matching UUIDs.
    candidates: list[dict] = []
    for rec in registry.sorted_records(include_archived=True):
        sid = rec.get("session_id", "")
        if sid.startswith(prefix):
            candidates.append(rec)
    if not candidates:
        return Resolution(NOT_FOUND, None, [])
    if len(candidates) == 1:
        return Resolution(OK, candidates[0]["session_id"], [])
    return Resolution(AMBIGUOUS, None, candidates)


def print_ambiguous(prefix: str, candidates: list[dict], stream=None) -> None:
    """Write the ambiguous-prefix candidate list to stderr (default)."""
    stream = stream or sys.stderr
    stream.write(f"cst: ambiguous prefix '{prefix}'; candidates:\n")
    for r in candidates:
        sid = r.get("session_id", "")
        short = sid[:8]
        pri = r.get("priority", "medium")
        title = r.get("title", "") or ""
        proj = r.get("project_name", "") or ""
        stream.write(f"  {short}  {pri}  {title}  ({proj})\n")


def resolve_or_exit(prefix: str) -> str:
    """Convenience: resolve and emit + sys.exit on error cases.

    Returns the resolved session_id on OK. Otherwise prints the
    appropriate message and calls sys.exit(code).
    """
    res = resolve(prefix)
    if res.status == OK:
        return res.session_id  # type: ignore[return-value]
    if res.status == TOO_SHORT:
        sys.stderr.write(
            "cst: session id must be the full UUID or a prefix of "
            "at least 6 hex characters\n"
        )
        sys.exit(2)
    if res.status == AMBIGUOUS:
        print_ambiguous(prefix, res.candidates)
        sys.exit(3)
    # NOT_FOUND
    sys.stderr.write(f"cst: no such session: {prefix}\n")
    sys.exit(1)
