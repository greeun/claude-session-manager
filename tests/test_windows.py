"""Tests for windows.open_short_ids — the hot-path liveness probe.

The refresh loop must not call AppleScript / EnumWindows / etc. on the
hot path; those are reserved for on-demand lookups (e.g. csm focus).
These tests pin the public contract and ensure the expensive helpers
are never invoked from ``open_short_ids``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import registry  # noqa: E402
import windows  # noqa: E402


_VALID_UUID = "12345678-1234-1234-1234-123456789012"


def _install_tmp_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CSM_REGISTRY_DIR", str(tmp_path))
    # Registry caches its dir on first touch — force a fresh resolve.
    monkeypatch.setattr(registry, "registry_dir", lambda: tmp_path, raising=True)
    return tmp_path


def _write_rec(tmp: Path, sid: str, **terminal_overrides):
    rec = registry.new_record(sid)
    rec["terminal"].update(terminal_overrides)
    (tmp / f"{sid}.json").write_text(
        __import__("json").dumps(rec), encoding="utf-8"
    )


def test_open_short_ids_skips_legacy_records_without_pid_or_tty(tmp_path, monkeypatch):
    _install_tmp_registry(tmp_path, monkeypatch)
    _write_rec(tmp_path, _VALID_UUID)  # no pid, no tty
    # Must not raise, must not hit scrape_visible_titles — patch it as a tripwire.
    monkeypatch.setattr(
        windows, "scrape_visible_titles",
        lambda: pytest.fail("scrape_visible_titles called on hot path"),
    )
    monkeypatch.setattr(
        windows, "_macos_titles",
        lambda: pytest.fail("_macos_titles called on hot path"),
    )
    assert windows.open_short_ids() == set()


def test_open_short_ids_reports_live_pid(tmp_path, monkeypatch):
    _install_tmp_registry(tmp_path, monkeypatch)
    my_pid = os.getpid()
    _write_rec(tmp_path, _VALID_UUID, pid=my_pid)
    result = windows.open_short_ids()
    assert _VALID_UUID[:8] in result


def test_open_short_ids_excludes_dead_pid(tmp_path, monkeypatch):
    _install_tmp_registry(tmp_path, monkeypatch)
    # A pid that is extraordinarily unlikely to exist.
    _write_rec(tmp_path, _VALID_UUID, pid=0x7FFFFFFE)
    assert _VALID_UUID[:8] not in windows.open_short_ids()


def test_open_short_ids_filters_dev_tty_sentinel(tmp_path, monkeypatch):
    """Legacy records captured with literal tty='/dev/tty' are unusable
    as identifiers — they must not be intersected with a ps snapshot."""
    _install_tmp_registry(tmp_path, monkeypatch)
    _write_rec(tmp_path, _VALID_UUID, tty="/dev/tty")
    monkeypatch.setattr(
        windows, "_ps_active_ttys",
        lambda: pytest.fail("ps probed despite unusable tty"),
    )
    assert windows.open_short_ids() == set()


def test_pid_alive_rejects_invalid():
    assert windows._pid_alive(None) is False
    assert windows._pid_alive(0) is False
    assert windows._pid_alive(-1) is False
    assert windows._pid_alive(os.getpid()) is True
