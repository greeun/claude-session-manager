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
