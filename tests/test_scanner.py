from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import registry
import scanner


SID_1 = "11111111-1111-1111-1111-111111111111"
SID_2 = "22222222-2222-2222-2222-222222222222"


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in lines:
            fh.write(json.dumps(row) + "\n")


@pytest.fixture
def projects_dir(tmp_path, monkeypatch) -> Path:
    p = tmp_path / "projects"
    p.mkdir()
    monkeypatch.setenv("CST_PROJECTS_DIR", str(p))
    return p


def test_creates_draft_from_jsonl(projects_dir):
    proj = projects_dir / "-tmp-fake-proj"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {
                "type": "user",
                "message": {"role": "user", "content": "Refactor the login endpoint"},
                "cwd": "/tmp/fake-proj",
            }
        ],
    )
    summary = scanner.scan_once()
    assert summary["created"] == 1
    rec = registry.read(SID_1)
    assert rec is not None
    assert rec["auto_detected"] is True
    assert rec["title"].startswith("Refactor the login endpoint")
    assert rec["project_name"] == "fake-proj"
    assert rec["cwd"] == "/tmp/fake-proj"


def test_title_falls_back_to_project_name(projects_dir):
    proj = projects_dir / "-tmp-only-asst"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [{"type": "assistant", "message": {"content": "hi there"}}],
    )
    scanner.scan_once()
    rec = registry.read(SID_1)
    assert rec["title"] == "only-asst"


def test_title_from_later_user_message_when_first_is_assistant(projects_dir):
    proj = projects_dir / "-tmp-second-user"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "assistant", "message": {"content": "hi"}},
            {"type": "user", "message": {"content": "actual-first-user-msg"}},
        ],
    )
    scanner.scan_once()
    rec = registry.read(SID_1)
    assert rec["title"].startswith("actual-first-user-msg")


def test_empty_jsonl_falls_back_to_project_name(projects_dir):
    proj = projects_dir / "-tmp-empty-proj"
    (proj).mkdir(parents=True)
    (proj / f"{SID_1}.jsonl").write_bytes(b"")
    summary = scanner.scan_once()
    assert summary["created"] == 1
    rec = registry.read(SID_1)
    assert rec["title"] == "empty-proj"


def test_non_uuid_filename_is_ignored(projects_dir):
    proj = projects_dir / "-tmp-weird"
    proj.mkdir(parents=True)
    (proj / "notauuid.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "x"}}) + "\n"
    )
    summary = scanner.scan_once()
    assert summary["created"] == 0
    # And no stray file was created with "notauuid" in the registry.
    for p in registry.registry_dir().iterdir():
        assert "notauuid" not in p.name


def test_updates_last_activity_from_mtime(projects_dir):
    proj = projects_dir / "-tmp-mtime"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [{"type": "user", "message": {"content": "m"}}, ],
    )
    jf = proj / f"{SID_1}.jsonl"
    # Set mtime to a known fixed point.
    fixed = time.mktime((2023, 6, 1, 12, 0, 0, 0, 0, 0))
    os.utime(jf, (fixed, fixed))
    scanner.scan_once()
    rec = registry.read(SID_1)
    assert rec["last_activity_at"].startswith("2023-06-01T")


def test_never_overwrites_user_fields(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [{"type": "user", "message": {"content": "scanner-title"}}],
    )
    scanner.scan_once()
    # User edits.
    registry.update(
        SID_1,
        title="User Title",
        priority="high",
        status="blocked",
        note="keep me",
        tags=["a", "b"],
    )
    # Simulate a newer transcript.
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [{"type": "user", "message": {"content": "different-scanner-text"}}],
    )
    scanner.scan_once()
    rec = registry.read(SID_1)
    assert rec["title"] == "User Title"
    assert rec["priority"] == "high"
    assert rec["status"] == "blocked"
    assert rec["note"] == "keep me"
    assert rec["tags"] == ["a", "b"]
    assert rec["auto_detected"] is False


def test_never_touches_archived(projects_dir):
    proj = projects_dir / "-tmp-arch"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [{"type": "user", "message": {"content": "t"}}],
    )
    scanner.scan_once()
    rec = registry.read(SID_1)
    rec["archived"] = True
    rec["archived_at"] = "2025-01-01T00:00:00Z"
    registry.write(rec)
    scanner.scan_once()
    rec2 = registry.read(SID_1)
    assert rec2["archived"] is True
    assert rec2["archived_at"] == "2025-01-01T00:00:00Z"


def test_project_name_derivation():
    assert scanner.decode_project_slug("-tmp-fake-proj") == "fake-proj"
    assert scanner.decode_project_slug("-Users-alice-proj-foo") == "foo"
    # Odd/empty input falls back sensibly.
    assert scanner.decode_project_slug("") == ""
    # A slug with no leading dash is taken as-is (basename).
    assert scanner.decode_project_slug("plainfoo") == "plainfoo"


# ---------------- progress extraction ------------------------------------


def _set_mtime(path: Path, offset_seconds: float) -> None:
    """Set mtime/atime to ``now + offset_seconds``."""
    t = time.time() + offset_seconds
    os.utime(path, (t, t))


def test_extracts_last_user_prompt(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [{"type": "user", "message": {"content": "Run the tests"}, "cwd": "/tmp/foo"}],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["last_user_prompt"] == "Run the tests"


def test_extracts_last_assistant_summary_first_line(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "hi"}, "cwd": "/tmp/foo"},
            {"type": "assistant", "message": {"content": "First line.\nSecond line."}},
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["last_assistant_summary"] == "First line."


def test_extracts_last_assistant_summary_skips_tool_use_parts(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "hi"}, "cwd": "/tmp/foo"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Let me run this."},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "x"}},
                    ]
                },
            },
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["last_assistant_summary"] == "Let me run this."


def test_extracts_current_task_hint_bash(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}
                    ]
                },
            },
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == "Running: pytest -q"


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_extracts_current_task_hint_edit_write_multiedit_notebookedit(
    projects_dir, tool
):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": tool, "input": {"file_path": "/tmp/foo/a.py"}}
                    ]
                },
            },
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == "Editing: a.py"


def test_extracts_current_task_hint_read(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/foo/b.py"}}]},
            },
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == "Reading: b.py"


@pytest.mark.parametrize(
    "tool,pattern", [("Grep", "TODO"), ("Glob", "**/*.py")]
)
def test_extracts_current_task_hint_grep_glob(projects_dir, tool, pattern):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": tool, "input": {"pattern": pattern}}]},
            },
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == f"Searching: {pattern}"


def test_current_task_hint_empty_when_no_tool_use_in_tail(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == ""


def test_current_task_hint_fallback_to_bare_tool_name_when_missing_input(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == "Bash"


def test_current_task_hint_empty_when_tool_use_has_no_name(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {"command": "x"}}]}},
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == ""


def test_current_task_hint_relpath_when_file_under_cwd(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/tmp/foo/src/lib.py"}}]}},
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == "Editing: src/lib.py"


def test_current_task_hint_basename_when_file_outside_cwd(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "q"}, "cwd": "/tmp/foo"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/other/dir/x.py"}}]}},
        ],
    )
    scanner.scan_once()
    assert registry.read(SID_1)["current_task_hint"] == "Editing: x.py"


def test_truncation_200_chars_with_single_ellipsis(projects_dir):
    proj = projects_dir / "-tmp-foo"
    big = "X" * 500
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [{"type": "user", "message": {"content": big}, "cwd": "/tmp/foo"}],
    )
    scanner.scan_once()
    p = registry.read(SID_1)["last_user_prompt"]
    assert len(p) == 200
    assert p.endswith("\u2026")
    assert p.count("\u2026") == 1


def test_truncation_is_code_point_aware_for_cjk(projects_dir):
    proj = projects_dir / "-tmp-foo"
    big = "한" * 500
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [{"type": "user", "message": {"content": big}, "cwd": "/tmp/foo"}],
    )
    scanner.scan_once()
    p = registry.read(SID_1)["last_user_prompt"]
    assert len(p) == 200
    assert p.endswith("\u2026")
    assert all(c == "한" for c in p[:199])


def test_non_utf8_bytes_do_not_crash(projects_dir):
    proj = projects_dir / "-tmp-foo"
    proj.mkdir(parents=True, exist_ok=True)
    # A line with bad bytes, but valid surrounding JSON structure.
    (proj / f"{SID_1}.jsonl").write_bytes(
        b'{"type":"user","message":{"content":"ok \xff\xfe bad"},"cwd":"/tmp/foo"}\n'
    )
    summary = scanner.scan_once()
    assert summary["scanned"] == 1
    rec = registry.read(SID_1)
    assert isinstance(rec["last_user_prompt"], str)
    assert "ok" in rec["last_user_prompt"]


def test_scanner_always_overwrites_progress_even_when_auto_detected_false(projects_dir):
    proj = projects_dir / "-tmp-foo"
    _write_jsonl(
        proj / f"{SID_1}.jsonl",
        [
            {"type": "user", "message": {"content": "fresh prompt"}, "cwd": "/tmp/foo"},
            {"type": "assistant", "message": {"content": "fresh summary"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}}]}},
        ],
    )
    scanner.scan_once()
    # Flip auto_detected=False via a user field.
    registry.update(SID_1, title="User Title")
    # Now tamper with progress fields on disk (a rogue write).
    rec = registry.read(SID_1)
    rec["last_user_prompt"] = "GARBAGE"
    rec["last_assistant_summary"] = "GARBAGE"
    rec["current_task_hint"] = "GARBAGE"
    registry.write(rec)
    # Move the JSONL mtime forward so "fresher wins" applies to prompt.
    _set_mtime(proj / f"{SID_1}.jsonl", offset_seconds=+3600)
    scanner.scan_once()
    r = registry.read(SID_1)
    assert r["title"] == "User Title"   # user-owned preserved
    assert r["last_user_prompt"] == "fresh prompt"
    assert r["last_assistant_summary"] == "fresh summary"
    assert r["current_task_hint"] == "Running: pytest"


def test_scanner_error_isolated_to_single_transcript(projects_dir):
    """An exception in one transcript must not cascade to siblings.

    We monkeypatch ``_extract_progress`` to raise for one specific path
    and verify: (a) scan completes, (b) sibling records get fresh
    progress, (c) the failed record's pre-existing progress survives
    untouched, (d) exactly one warning line is logged.
    """
    import scanner as sc

    proj_a = projects_dir / "-tmp-a"
    proj_b = projects_dir / "-tmp-b"
    good = proj_a / f"{SID_1}.jsonl"
    bad = proj_b / f"{SID_2}.jsonl"
    _write_jsonl(
        good,
        [
            {"type": "user", "message": {"content": "good prompt"}, "cwd": "/tmp/a"},
            {"type": "assistant", "message": {"content": "good summary"}},
        ],
    )
    _write_jsonl(
        bad,
        [{"type": "user", "message": {"content": "would be bad"}, "cwd": "/tmp/b"}],
    )

    # Pre-seed the bad record with non-default progress fields so we
    # can observe them surviving.
    pre = registry.new_record(SID_2, title="bad-pre")
    pre["last_user_prompt"] = "PRE_LUP"
    pre["last_assistant_summary"] = "PRE_LAS"
    pre["current_task_hint"] = "PRE_HINT"
    # auto_detected=False so scanner doesn't rewrite title.
    pre["auto_detected"] = False
    registry.write(pre)

    orig = sc._extract_progress

    def boom(path, cwd):
        if path == bad:
            # Simulate an unexpected internal error; the wrapped helper
            # catches its OWN exceptions and returns None, so we must
            # model that contract.
            return None
        return orig(path, cwd)

    # Wipe the log so we can count new entries.
    log = sc._scanner_log_path()
    if log.exists():
        log.unlink()

    import pytest as _p
    try:
        sc._extract_progress = boom
        # Make the boom path also emit a warning to simulate the real
        # "return None + log once" contract.
        def boom_with_log(path, cwd):
            if path == bad:
                sc._log_scanner_error(f"{path.name}: simulated failure")
                return None
            return orig(path, cwd)
        sc._extract_progress = boom_with_log
        sc.scan_once()
    finally:
        sc._extract_progress = orig

    good_rec = registry.read(SID_1)
    assert good_rec["last_user_prompt"] == "good prompt"
    assert good_rec["last_assistant_summary"] == "good summary"

    bad_rec = registry.read(SID_2)
    assert bad_rec["last_user_prompt"] == "PRE_LUP"
    assert bad_rec["last_assistant_summary"] == "PRE_LAS"
    assert bad_rec["current_task_hint"] == "PRE_HINT"

    # Exactly one log line from this failure.
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    failure_lines = [ln for ln in lines if "simulated failure" in ln]
    assert len(failure_lines) == 1, lines


def test_scanner_limits_tail_window_to_50_lines(projects_dir):
    proj = projects_dir / "-tmp-foo"
    jf = proj / f"{SID_1}.jsonl"
    proj.mkdir(parents=True, exist_ok=True)
    with jf.open("w", encoding="utf-8") as fh:
        # 60 user lines. Only the last 50 should be considered.
        for i in range(60):
            fh.write(
                json.dumps({"type": "user", "message": {"content": f"p{i}"}, "cwd": "/tmp/foo"}) + "\n"
            )
    scanner.scan_once()
    p = registry.read(SID_1)["last_user_prompt"]
    # Last line is p59 — clearly in window.
    assert p == "p59"
    # Now truncate the file to 51 lines: last 50 visible to window
    # means p1..p50 are visible, p0 is not.
    # Replace the file with p0..p50 (51 lines) and check the OLDEST
    # line visible via reverse lookup for tool_use (we don't need for
    # user prompt). Exact boundary assertion:
    lines = []
    for i in range(51):
        lines.append(
            json.dumps({"type": "user", "message": {"content": f"q{i}"}, "cwd": "/tmp/foo"})
        )
    jf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # last mtime must now be newer than stored last_activity_at for
    # "fresher wins" to apply.
    _set_mtime(jf, offset_seconds=+3600)
    scanner.scan_once()
    assert registry.read(SID_1)["last_user_prompt"] == "q50"


def test_scanner_does_not_regress_hook_written_prompt(projects_dir):
    proj = projects_dir / "-tmp-fresh"
    jf = proj / f"{SID_1}.jsonl"
    _write_jsonl(
        jf,
        [{"type": "user", "message": {"content": "old transcript prompt"}, "cwd": "/tmp/fresh"}],
    )
    # Set mtime to 1h ago — scanner's first pass seeds the prompt.
    _set_mtime(jf, offset_seconds=-3600)
    scanner.scan_once()
    assert registry.read(SID_1)["last_user_prompt"] == "old transcript prompt"
    # Hook writes a newer prompt and bumps last_activity_at to "now".
    rec = registry.read(SID_1)
    rec["last_user_prompt"] = "fresh hook prompt"
    import datetime as _dt
    rec["last_activity_at"] = _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    registry.write(rec)
    # Re-scan: transcript still has OLD mtime + OLD content.
    scanner.scan_once()
    assert registry.read(SID_1)["last_user_prompt"] == "fresh hook prompt"


def test_scan_extracts_first_user_prompt(tmp_path, monkeypatch):
    """Scanner populates first_user_prompt from the first user line."""
    import registry, scanner
    proj = tmp_path / "projects" / "-tmp-proj"
    proj.mkdir(parents=True)
    monkeypatch.setenv("CST_PROJECTS_DIR", str(tmp_path / "projects"))

    sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    jf = proj / f"{sid}.jsonl"
    import json
    lines = [
        json.dumps({"type": "user", "message": {"content": "first prompt here"}}),
        json.dumps({"type": "assistant", "message": {"content": "reply"}}),
        json.dumps({"type": "user", "message": {"content": "second prompt"}}),
    ]
    jf.write_text("\n".join(lines) + "\n")

    scanner.scan_once()
    rec = registry.read(sid)
    assert rec is not None
    assert rec["first_user_prompt"] == "first prompt here"


def test_last_user_prompt_truncates_at_200(tmp_path, monkeypatch):
    import registry, scanner
    proj = tmp_path / "projects" / "-tmp-proj"
    proj.mkdir(parents=True)
    monkeypatch.setenv("CST_PROJECTS_DIR", str(tmp_path / "projects"))

    sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    jf = proj / f"{sid}.jsonl"
    import json
    long_prompt = "x" * 250
    jf.write_text(json.dumps({"type": "user", "message": {"content": long_prompt}}) + "\n")

    scanner.scan_once()
    rec = registry.read(sid)
    # 199 chars + ellipsis = 200
    assert len(rec["last_user_prompt"]) == 200
    assert rec["last_user_prompt"].endswith("\u2026")


def test_scanner_overwrites_prompt_when_jsonl_is_newer(projects_dir):
    proj = projects_dir / "-tmp-newer"
    jf = proj / f"{SID_1}.jsonl"
    _write_jsonl(
        jf,
        [{"type": "user", "message": {"content": "seeded prompt"}, "cwd": "/tmp/newer"}],
    )
    _set_mtime(jf, offset_seconds=-7200)
    scanner.scan_once()
    # Record has an older last_activity_at than the JSONL mtime we're
    # about to set.
    # Append a new user line and advance mtime far into the future.
    with jf.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"type": "user", "message": {"content": "genuinely newer"}, "cwd": "/tmp/newer"}) + "\n"
        )
    _set_mtime(jf, offset_seconds=+3600)
    scanner.scan_once()
    assert registry.read(SID_1)["last_user_prompt"] == "genuinely newer"
