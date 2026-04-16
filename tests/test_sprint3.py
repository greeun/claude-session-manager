"""Sprint 3 tests: slash commands (metadata) + watch TUI render + installer symlinks."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
COMMANDS_DIR = SKILL_DIR / "commands"

EXPECTED_COMMANDS = {
    "tasks.md",
    "task-register.md",
    "task-note.md",
    "task-priority.md",
    "task-status.md",
    "task-done.md",
    "task-focus.md",
    "done.md",
}


def test_all_slash_commands_present():
    found = {p.name for p in COMMANDS_DIR.glob("*.md")}
    assert EXPECTED_COMMANDS <= found, f"missing: {EXPECTED_COMMANDS - found}"


@pytest.mark.parametrize("name", sorted(EXPECTED_COMMANDS))
def test_slash_command_has_frontmatter_and_description(name):
    text = (COMMANDS_DIR / name).read_text()
    assert text.startswith("---\n"), f"{name}: missing frontmatter"
    head, _, _ = text[4:].partition("\n---\n")
    assert re.search(r"^description:\s*\S", head, re.M), f"{name}: missing description"
    assert "allowed-tools:" in head, f"{name}: missing allowed-tools"


@pytest.mark.parametrize("name", sorted(EXPECTED_COMMANDS))
def test_slash_command_invokes_cst(name):
    text = (COMMANDS_DIR / name).read_text()
    assert "csm" in text, f"{name}: body does not reference cst"


# --- watch TUI render ------------------------------------------------------ #

def test_watch_render_empty_returns_no_sessions():
    import watch
    out = watch.render([], 0)
    assert "(no sessions)" in out


def test_watch_render_shows_title_and_progress():
    import watch, registry
    r = registry.new_record(session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    r["title"] = "Test Title"
    r["priority"] = "high"
    r["last_user_prompt"] = "fix login"
    r["current_task_hint"] = "Running: pytest"
    registry.write(r)
    rows = watch._load_rows()
    out = watch.render(rows, 0)
    assert "Test Title" in out
    assert "fix login" in out
    assert "Running: pytest" in out
    assert "▶" in out


def test_watch_render_marks_live_vs_idle(monkeypatch):
    import watch, registry, livedot
    r = registry.new_record(session_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    r["terminal"] = {"tty": "/dev/ttys001"}
    registry.write(r)
    monkeypatch.setattr(livedot, "live_ttys", lambda: {"/dev/ttys001"})
    rows = watch._load_rows()
    out = watch.render(rows, 0)
    assert "●" in out


def test_watch_requires_tty(monkeypatch):
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import watch, io
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    rc = watch.run()
    assert rc == 2


# --- pin (AppleScript) non-darwin guard ------------------------------------ #

def test_pin_returns_6_when_no_terminal_available(monkeypatch):
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import watch
    # Simulate an environment with NO terminal CLI available.
    monkeypatch.setattr(watch.shutil, "which", lambda x: None)
    monkeypatch.setattr(sys, "platform", "linux")
    assert watch.pin_in_iterm() == 6


# --- installer symlinks commands ------------------------------------------ #

def test_install_symlinks_slash_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = subprocess.call(
        ["bash", str(SKILL_DIR / "install.sh")],
        env={**os.environ, "HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert rc == 0
    linked = {p.name for p in (tmp_path / ".claude" / "commands").glob("*.md")}
    assert EXPECTED_COMMANDS <= linked


# --- tooltip content ------------------------------------------------------ #

def test_tooltip_content_returns_formatted_lines():
    import watch, registry
    sid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    r = registry.new_record(session_id=sid)
    r["title"] = "Fix auth bug"
    r["created_at"] = "2026-04-14T15:03:27.041923Z"
    r["done_at"] = "2026-04-16T10:30:00.000000Z"
    r["first_user_prompt"] = "Please fix the login page auth"
    r["last_user_prompt"] = "Now handle the edge case for expired tokens"
    r["last_assistant_summary"] = "Fixed the token expiry handler"
    registry.write(r)

    lines = watch._tooltip_lines(r, width=60)
    assert any("2026-04-14" in l for l in lines), "created_at date missing"
    assert any("2026-04-16" in l for l in lines), "done_at date missing"
    assert any(l.startswith("SP ") for l in lines), "SP line missing"
    assert any("login page auth" in l for l in lines), "first_user_prompt missing"
    assert any(l.startswith("EP ") for l in lines), "EP line missing"
    assert any("expired tokens" in l for l in lines), "last_user_prompt missing"
    assert any(l.startswith("ER ") for l in lines), "ER line missing"
    assert any("token expiry handler" in l for l in lines), "last_assistant_summary missing"


def test_tooltip_content_omits_done_when_none():
    import watch, registry
    sid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    r = registry.new_record(session_id=sid)
    r["title"] = "WIP task"
    r["created_at"] = "2026-04-14T15:03:27.041923Z"
    r["first_user_prompt"] = "start working"
    r["last_user_prompt"] = "continue"
    registry.write(r)

    lines = watch._tooltip_lines(r, width=60)
    assert any("2026-04-14" in l for l in lines)
    assert not any("Done" in l for l in lines)


# --- installer symlinks commands ------------------------------------------ #

def test_install_preserves_existing_command_file(tmp_path):
    # A user's unrelated /tasks regular file should not be clobbered.
    cmd_dir = tmp_path / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "tasks.md").write_text("USER CONTENT")
    rc = subprocess.call(
        ["bash", str(SKILL_DIR / "install.sh")],
        env={**os.environ, "HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert rc == 0
    assert (cmd_dir / "tasks.md").read_text() == "USER CONTENT"
    # Other commands still installed.
    assert (cmd_dir / "task-done.md").is_symlink()
