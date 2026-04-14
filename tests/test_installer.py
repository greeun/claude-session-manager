from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import INSTALL_SH, SKILL_ROOT


def _run_install(home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    # Don't let a stale CST_REGISTRY_DIR leak into the installer's cst smoke test.
    env.pop("CST_REGISTRY_DIR", None)
    return subprocess.run(
        ["bash", str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(SKILL_ROOT),
    )


def _hook_commands(settings: dict, event: str) -> list[str]:
    out: list[str] = []
    for m in settings.get("hooks", {}).get(event, []):
        for h in m.get("hooks", []):
            c = h.get("command")
            if isinstance(c, str):
                out.append(c)
    return out


def test_install_idempotent(tmp_path):
    home = tmp_path / "install_home"
    home.mkdir()

    r1 = _run_install(home)
    assert r1.returncode == 0, r1.stderr + r1.stdout
    r2 = _run_install(home)
    assert r2.returncode == 0, r2.stderr + r2.stdout

    settings = json.loads((home / ".claude/settings.json").read_text())
    assert _hook_commands(settings, "SessionStart").count("cst hook session-start") == 1
    assert _hook_commands(settings, "UserPromptSubmit").count("cst hook activity") == 1


def test_install_from_missing_settings_json(tmp_path):
    home = tmp_path / "install_home"
    home.mkdir()
    # Pre-create only empty .claude/ (no settings.json).
    (home / ".claude").mkdir()
    r = _run_install(home)
    assert r.returncode == 0, r.stderr + r.stdout
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert "SessionStart" in settings["hooks"]
    assert "UserPromptSubmit" in settings["hooks"]


def test_install_from_missing_claude_dir(tmp_path):
    home = tmp_path / "install_home"
    home.mkdir()
    # No .claude at all.
    r = _run_install(home)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (home / ".claude").is_dir()
    assert (home / ".claude/claude-tasks").is_dir()


def test_install_preserves_existing_statusline(tmp_path):
    home = tmp_path / "install_home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text(
        json.dumps(
            {"statusLine": {"type": "command", "command": "echo MY_EXISTING"}}
        )
    )
    r = _run_install(home)
    assert r.returncode == 0, r.stderr + r.stdout
    s = json.loads((home / ".claude/settings.json").read_text())
    assert s["statusLine"]["command"] == "echo MY_EXISTING"
    # Warning emitted on stdout mentioning the existing statusline.
    assert "existing statusline" in (r.stdout + r.stderr).lower()
    # And hooks still got merged.
    assert "cst hook session-start" in _hook_commands(s, "SessionStart")


def test_install_sets_fresh_statusline(tmp_path):
    """When no statusLine exists, installer sets the full canonical shape."""
    home = tmp_path / "install_home"
    home.mkdir()
    r = _run_install(home)
    assert r.returncode == 0, r.stderr + r.stdout
    s = json.loads((home / ".claude/settings.json").read_text())
    sl = s["statusLine"]
    assert isinstance(sl, dict)
    assert set(sl.keys()) == {"type", "command", "padding"}
    assert sl["type"] == "command"
    assert sl["command"] == "cst statusline"
    assert sl["padding"] == 0


def test_install_idempotent_statusline(tmp_path):
    """Re-running the installer leaves a prior cst statusline untouched."""
    home = tmp_path / "install_home"
    home.mkdir()
    _run_install(home)
    _run_install(home)
    s = json.loads((home / ".claude/settings.json").read_text())
    sl = s["statusLine"]
    assert set(sl.keys()) == {"type", "command", "padding"}
    assert sl["type"] == "command"
    assert sl["command"] == "cst statusline"
    assert sl["padding"] == 0


def test_install_refuses_malformed_settings_json(tmp_path):
    home = tmp_path / "install_home"
    home.mkdir()
    (home / ".claude").mkdir()
    bad = home / ".claude/settings.json"
    bad.write_text("{not: valid")
    orig_bytes = bad.read_bytes()

    r = _run_install(home)
    assert r.returncode != 0, "installer must exit non-zero on malformed settings"
    assert bad.read_bytes() == orig_bytes, "installer must not modify the file"
    # And no backup was written (policy (a)).
    backups = list((home / ".claude").glob("settings.json.bak-*"))
    assert backups == []


def test_install_refuses_when_cst_bin_is_regular_file(tmp_path):
    home = tmp_path / "install_home"
    home.mkdir()
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    cst_bin = bin_dir / "cst"
    # Pre-seed a REGULAR file (not a symlink) at the target path.
    cst_bin.write_text("echo fake-existing-cst\n")
    orig_bytes = cst_bin.read_bytes()
    # Filesystem probes: settings.json should not exist yet and must not
    # be created by a refused install.
    settings = home / ".claude/settings.json"
    assert not settings.exists()

    r = _run_install(home)

    assert r.returncode != 0, "installer must exit non-zero"
    assert "refusing to overwrite" in (r.stderr + r.stdout)
    # The pre-existing regular file is untouched (byte-identical, still a regular file).
    assert cst_bin.is_file() and not cst_bin.is_symlink()
    assert cst_bin.read_bytes() == orig_bytes
    # No side effects on disk: settings.json was never created.
    assert not settings.exists()


def test_install_replaces_existing_symlink(tmp_path):
    """Idempotent re-install: an existing symlink at ${CST_BIN} is replaced."""
    home = tmp_path / "install_home"
    home.mkdir()
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    cst_bin = bin_dir / "cst"
    # Pre-seed a (stale) symlink pointing elsewhere.
    os.symlink("/tmp/some-old-target", cst_bin)
    assert cst_bin.is_symlink()

    r = _run_install(home)
    assert r.returncode == 0, r.stderr + r.stdout
    assert cst_bin.is_symlink()
    # Link now points at our cst.py.
    assert str(cst_bin.resolve()).endswith("scripts/cst.py")


def test_install_does_not_treat_substring_match_as_duplicate(tmp_path):
    home = tmp_path / "install_home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "cst hook session-start --debug",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    r = _run_install(home)
    assert r.returncode == 0, r.stderr + r.stdout
    s = json.loads((home / ".claude/settings.json").read_text())
    cmds = _hook_commands(s, "SessionStart")
    # The --debug entry must survive AND the canonical entry must be added.
    assert "cst hook session-start --debug" in cmds
    assert "cst hook session-start" in cmds
