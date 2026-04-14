from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import registry
from conftest import CST_PY


def _run(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CST_PY), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_list_empty_exits_zero():
    r = _run(["list"])
    assert r.returncode == 0, r.stderr
    assert "(no sessions)" in r.stdout


def test_version_flag():
    r = _run(["--version"])
    assert r.returncode == 0
    assert r.stdout.strip() == "cst 0.1.0"


def test_set_then_list():
    sid = "12345678-1234-1234-1234-1234567890ab"
    registry.write(registry.new_record(sid))
    r = _run(["set", sid, "--title", "HELLO", "--priority", "high"])
    assert r.returncode == 0, r.stderr
    r2 = _run(["list"])
    assert r2.returncode == 0
    assert "HELLO" in r2.stdout
    assert "high" in r2.stdout


def test_done_visible_archive_hidden():
    sid = "22222222-2222-2222-2222-222222222222"
    registry.write(registry.new_record(sid, title="T"))
    _run(["done", sid]).check_returncode()
    assert _run(["list"]).stdout.count(sid[:8]) == 1
    _run(["archive", sid]).check_returncode()
    assert _run(["list"]).stdout.count(sid[:8]) == 0
    assert _run(["list", "--all"]).stdout.count(sid[:8]) == 1


def test_list_sort_order_high_medium_low_then_recency():
    # Direct registry seeding with deterministic timestamps.
    def seed(sid: str, pri: str, ts: str, title: str):
        rec = registry.new_record(sid, priority=pri, title=title)
        rec["last_activity_at"] = ts
        registry.write(rec)

    seed("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1", "medium", "2025-01-01T00:00:10Z", "M-recent")
    seed("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2", "high",   "2025-01-01T00:00:05Z", "H-old")
    seed("cccccccc-cccc-cccc-cccc-ccccccccccc3", "high",   "2025-01-01T00:00:55Z", "H-recent")
    seed("dddddddd-dddd-dddd-dddd-ddddddddddd4", "low",    "2025-01-01T00:00:59Z", "L-newest")

    r = _run(["list", "--json"])
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    titles = [row["title"] for row in rows]
    assert titles == ["H-recent", "H-old", "M-recent", "L-newest"]


def test_list_json_schema():
    sid = "efefefef-efef-efef-efef-efefefefefef"
    registry.write(registry.new_record(sid, title="J"))
    r = _run(["list", "--json"])
    assert r.returncode == 0
    rows = json.loads(r.stdout)
    assert len(rows) == 1
    row = rows[0]
    for k in (
        "session_id",
        "short_id",
        "priority",
        "status",
        "title",
        "project_name",
        "last_activity_at",
        "archived",
    ):
        assert k in row, f"missing key: {k}"
    assert row["short_id"] == sid[:8]


def test_set_rejects_bad_priority():
    sid = "abababab-abab-abab-abab-abababababab"
    registry.write(registry.new_record(sid))
    r = _run(["set", sid, "--priority", "urgent"])
    assert r.returncode == 1
    assert "high|medium|low" in r.stderr


def test_set_unknown_id():
    r = _run(["set", "ffffffff-ffff-ffff-ffff-ffffffffffff", "--title", "x"])
    assert r.returncode == 1
    assert "no such session" in r.stderr
