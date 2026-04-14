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
    assert r.stdout.strip() == "csm 0.3.1"


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


def test_set_status_rejects_invalid_value():
    sid = "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd"
    registry.write(registry.new_record(sid, status="in_progress"))
    r = _run(["set", sid, "--status", "bogus_value"])
    assert r.returncode == 1
    assert "--status must be in_progress|blocked|waiting|done" in r.stderr
    # Record must NOT have been mutated.
    rec = registry.read(sid)
    assert rec["status"] == "in_progress"
    assert rec["auto_detected"] is True


@pytest.mark.parametrize("value", ["in_progress", "blocked", "waiting", "done"])
def test_set_status_accepts_all_valid_values(value):
    sid = "aeaeaeae-aeae-aeae-aeae-aeaeaeaeaeae"
    registry.write(registry.new_record(sid, status="in_progress"))
    r = _run(["set", sid, "--status", value])
    assert r.returncode == 0, r.stderr
    assert registry.read(sid)["status"] == value


# ---------------- Sprint 2 CLI tests ------------------------------------


import datetime as _dt
import hashlib


def _seed_record(sid: str, **over):
    rec = registry.new_record(sid)
    rec.update(over)
    registry.write(rec)
    return rec


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_list_multiline_shows_arrow_and_gear():
    sid = "22222222-3333-4444-5555-666666666666"
    rec = registry.new_record(sid, title="T", priority="medium")
    rec["last_user_prompt"] = "Fix login bug"
    rec["current_task_hint"] = "Running: pytest -q"
    registry.write(rec)
    r = _run(["list"])
    assert r.returncode == 0, r.stderr
    assert "\u2937 Fix login bug" in r.stdout
    assert "\u2699 Running: pytest -q" in r.stdout


def test_list_compact_strips_subrows_one_line_per_session():
    sid = "22222222-3333-4444-5555-666666666666"
    rec = registry.new_record(sid, title="T", priority="medium")
    rec["last_user_prompt"] = "Fix login bug"
    rec["current_task_hint"] = "Running: pytest"
    registry.write(rec)
    r = _run(["list", "--compact"])
    assert r.returncode == 0, r.stderr
    assert "\u2937" not in r.stdout
    assert "\u2699" not in r.stdout
    # No stale banner (non-stale record), no empty lines.
    nonblank = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(nonblank) == 1


def test_list_json_includes_new_progress_fields_and_live():
    sid = "11aa22bb-33cc-44dd-55ee-66ff77aa88bb"
    rec = registry.new_record(sid, title="JX")
    rec["last_user_prompt"] = "hello"
    rec["last_assistant_summary"] = "sure"
    rec["current_task_hint"] = "Editing: x.py"
    registry.write(rec)
    r = _run(["list", "--json"])
    assert r.returncode == 0
    rows = json.loads(r.stdout)
    row = [x for x in rows if x["session_id"] == sid][0]
    for k in (
        "last_user_prompt",
        "last_assistant_summary",
        "current_task_hint",
        "live",
    ):
        assert k in row
    assert row["last_user_prompt"] == "hello"
    assert row["last_assistant_summary"] == "sure"
    assert row["current_task_hint"] == "Editing: x.py"
    assert row["live"] is False


def test_set_rejects_progress_field_flags():
    sid = "12345678-dead-beef-1234-567890abcdef"
    registry.write(registry.new_record(sid))
    for flag in ("--last-user-prompt", "--last-assistant-summary", "--current-task-hint"):
        r = _run(["set", sid, flag, "x"])
        assert r.returncode != 0, (flag, r.stdout, r.stderr)


def _age_record_hours_ago(sid: str, hours: float) -> None:
    rec = registry.read(sid)
    rec["last_activity_at"] = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec["status"] = "in_progress"
    rec["archived"] = False
    registry.write(rec)


def test_list_stale_banner_appears():
    sid = "abcabcab-cabc-abca-bcab-cabcabcabcab"
    registry.write(registry.new_record(sid, title="old"))
    _age_record_hours_ago(sid, 5)
    r = _run(["list"])
    assert r.returncode == 0
    assert r.stdout.startswith(("\u26a0", "\u25cf", "\u25cb")) or "\u26a0" in r.stdout
    assert "\u26a0" in r.stdout
    assert "stale" in r.stdout
    assert "run 'csm review-stale'" in r.stdout


def test_list_stale_flag_filters():
    sid_stale = "11111111-1111-aaaa-bbbb-111111111111"
    sid_fresh = "22222222-2222-aaaa-bbbb-222222222222"
    registry.write(registry.new_record(sid_stale, title="old"))
    _age_record_hours_ago(sid_stale, 5)
    registry.write(registry.new_record(sid_fresh, title="new"))
    r = _run(["list", "--stale"])
    assert r.returncode == 0
    assert sid_stale[:8] in r.stdout
    assert sid_fresh[:8] not in r.stdout


def test_list_stale_label_overrides_displayed_status_but_not_stored():
    sid = "33333333-3333-aaaa-bbbb-333333333333"
    registry.write(registry.new_record(sid, title="T"))
    _age_record_hours_ago(sid, 5)
    r = _run(["list"])
    assert "stale" in r.stdout
    # Stored status is unchanged.
    assert registry.read(sid)["status"] == "in_progress"


def test_json_omits_stale_banner_across_flag_combos():
    sid = "44444444-5555-aaaa-bbbb-444444444444"
    registry.write(registry.new_record(sid, title="T"))
    _age_record_hours_ago(sid, 5)
    for flags in ([], ["--all"], ["--stale"], ["--all", "--stale"]):
        r = _run(["list", *flags, "--json"])
        assert r.returncode == 0, (flags, r.stderr)
        assert "\u26a0" not in r.stdout
        assert "csm review-stale" not in r.stdout
        rows = json.loads(r.stdout)
        for row in rows:
            for k in (
                "session_id",
                "short_id",
                "priority",
                "status",
                "title",
                "project_name",
                "last_activity_at",
                "archived",
                "last_user_prompt",
                "last_assistant_summary",
                "current_task_hint",
                "live",
            ):
                assert k in row


def test_prefix_exact_full_uuid():
    sid = "99999999-aaaa-bbbb-cccc-dddddddddddd"
    registry.write(registry.new_record(sid, title="full"))
    r = _run(["set", sid, "--priority", "high"])
    assert r.returncode == 0, r.stderr
    assert registry.read(sid)["priority"] == "high"


def test_prefix_min_six_chars_exact_match():
    sid = "deadbeef-1111-2222-3333-444444444444"
    registry.write(registry.new_record(sid, title="p"))
    r = _run(["set", "deadbeef", "--priority", "low"])
    assert r.returncode == 0, r.stderr
    assert registry.read(sid)["priority"] == "low"


def test_prefix_too_short_exits_2():
    sid = "deadbeef-1111-2222-3333-444444444444"
    registry.write(registry.new_record(sid))
    r = _run(["set", "deadb", "--priority", "low"])
    assert r.returncode == 2
    assert "at least 6 hex" in r.stderr


def test_prefix_not_found_exits_1():
    r = _run(["set", "0123456789ab", "--priority", "low"])
    assert r.returncode == 1
    assert "no such session" in r.stderr


def test_prefix_ambiguous_exits_3_and_does_not_mutate(tmp_path):
    sid_a = "aabbccdd-1111-2222-3333-444455556666"
    sid_b = "aabbccdd-7777-8888-9999-aaaabbbbcccc"
    registry.write(registry.new_record(sid_a, title="A"))
    registry.write(registry.new_record(sid_b, title="B"))
    ha = _sha256(registry.record_path(sid_a))
    hb = _sha256(registry.record_path(sid_b))
    r = _run(["set", "aabbccdd", "--priority", "high"])
    assert r.returncode == 3
    assert "ambiguous" in r.stderr
    assert "aabbccdd" in r.stderr  # candidate short ids echoed
    # Neither file mutated.
    assert _sha256(registry.record_path(sid_a)) == ha
    assert _sha256(registry.record_path(sid_b)) == hb


@pytest.mark.parametrize("subcmd", ["set", "done", "archive", "focus", "resume"])
def test_prefix_applies_to_done_archive_focus_resume(subcmd):
    """Every id-taking subcommand must use the resolver (exit 3 on ambig)."""
    sid_a = "aabbccdd-1111-2222-3333-444455556666"
    sid_b = "aabbccdd-7777-8888-9999-aaaabbbbcccc"
    registry.write(registry.new_record(sid_a, title="A"))
    registry.write(registry.new_record(sid_b, title="B"))
    ha = _sha256(registry.record_path(sid_a))
    hb = _sha256(registry.record_path(sid_b))
    # Force non-darwin so focus/resume can't succeed for unrelated reasons.
    env = {"CST_FORCE_PLATFORM": "linux"}
    if subcmd == "set":
        r = _run(["set", "aabbccdd", "--priority", "high"], env_extra=env)
    else:
        r = _run([subcmd, "aabbccdd"], env_extra=env)
    assert r.returncode == 3, (subcmd, r.stdout, r.stderr)
    assert "ambiguous" in r.stderr
    assert _sha256(registry.record_path(sid_a)) == ha
    assert _sha256(registry.record_path(sid_b)) == hb


def test_focus_non_macos_exits_6():
    sid = "cccccccc-dddd-eeee-ffff-000011112222"
    registry.write(registry.new_record(sid, title="x"))
    r = _run(["focus", sid], env_extra={"CST_FORCE_PLATFORM": "linux"})
    assert r.returncode == 6
    assert "only supported on macOS" in r.stderr


def test_resume_non_macos_exits_6():
    sid = "cccccccc-dddd-eeee-ffff-000011112222"
    rec = registry.new_record(sid, title="x")
    rec["cwd"] = "/tmp"
    registry.write(rec)
    r = _run(["resume", sid], env_extra={"CST_FORCE_PLATFORM": "linux"})
    assert r.returncode == 6
    assert "only supported on macOS" in r.stderr


def test_gc_empty_registry_exits_0_with_summary():
    r = _run(["gc"])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == (
        "csm gc: deleted 0 record(s); kept 0 archived record(s) "
        "still within the 7-day window"
    )


def test_statusline_empty_registry_prints_empty_line():
    r = _run(["statusline"])
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # only a newline
    assert "/tasks" not in r.stdout   # no arrow when zero pending
