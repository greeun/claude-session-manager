from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import config


def _write_config(home: Path, content: str) -> Path:
    p = home / ".claude" / "claude-tasks.config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_config_missing_uses_default_4h(monkeypatch):
    monkeypatch.delenv("CST_STALE_THRESHOLD_SECONDS", raising=False)
    monkeypatch.delenv("CST_CONFIG_PATH", raising=False)
    assert config.stale_threshold_seconds() == 14400


def test_config_overrides_threshold(monkeypatch, tmp_path):
    cp = tmp_path / "config.json"
    cp.write_text(json.dumps({"stale_threshold_seconds": 3600}))
    monkeypatch.setenv("CST_CONFIG_PATH", str(cp))
    monkeypatch.delenv("CST_STALE_THRESHOLD_SECONDS", raising=False)
    assert config.stale_threshold_seconds() == 3600


def test_config_malformed_falls_back_to_default_and_logs(monkeypatch, tmp_path):
    cp = tmp_path / "config.json"
    cp.write_text("not json")
    monkeypatch.setenv("CST_CONFIG_PATH", str(cp))
    monkeypatch.delenv("CST_STALE_THRESHOLD_SECONDS", raising=False)
    assert config.stale_threshold_seconds() == 14400
    log = config._log_path()
    assert log.exists()
    txt = log.read_text(encoding="utf-8")
    assert "claude-tasks.config.json" in txt


@pytest.mark.parametrize(
    "value",
    ["string-val", 3.14, [1, 2], {"x": 1}, None],
)
def test_config_bad_value_type_falls_back_to_default_and_logs(
    monkeypatch, tmp_path, value
):
    cp = tmp_path / "config.json"
    cp.write_text(json.dumps({"stale_threshold_seconds": value}))
    monkeypatch.setenv("CST_CONFIG_PATH", str(cp))
    monkeypatch.delenv("CST_STALE_THRESHOLD_SECONDS", raising=False)
    assert config.stale_threshold_seconds() == 14400
    txt = config._log_path().read_text(encoding="utf-8")
    assert "stale_threshold_seconds" in txt


def test_config_bool_rejected_even_though_isinstance_int(monkeypatch, tmp_path):
    cp = tmp_path / "config.json"
    cp.write_text(json.dumps({"stale_threshold_seconds": True}))
    monkeypatch.setenv("CST_CONFIG_PATH", str(cp))
    monkeypatch.delenv("CST_STALE_THRESHOLD_SECONDS", raising=False)
    assert config.stale_threshold_seconds() == 14400
    txt = config._log_path().read_text(encoding="utf-8")
    assert "stale_threshold_seconds" in txt
    assert "bool" in txt


@pytest.mark.parametrize("value", [0, -1, -3600])
def test_config_zero_or_negative_falls_back_to_default(monkeypatch, tmp_path, value):
    cp = tmp_path / "config.json"
    cp.write_text(json.dumps({"stale_threshold_seconds": value}))
    monkeypatch.setenv("CST_CONFIG_PATH", str(cp))
    monkeypatch.delenv("CST_STALE_THRESHOLD_SECONDS", raising=False)
    assert config.stale_threshold_seconds() == 14400
    txt = config._log_path().read_text(encoding="utf-8")
    assert "stale_threshold_seconds" in txt


def test_env_var_overrides_config_file(monkeypatch, tmp_path):
    cp = tmp_path / "config.json"
    cp.write_text(json.dumps({"stale_threshold_seconds": 3600}))
    monkeypatch.setenv("CST_CONFIG_PATH", str(cp))
    monkeypatch.setenv("CST_STALE_THRESHOLD_SECONDS", "60")
    assert config.stale_threshold_seconds() == 60


def test_env_var_invalid_falls_through_to_config(monkeypatch, tmp_path):
    cp = tmp_path / "config.json"
    cp.write_text(json.dumps({"stale_threshold_seconds": 3600}))
    monkeypatch.setenv("CST_CONFIG_PATH", str(cp))
    monkeypatch.setenv("CST_STALE_THRESHOLD_SECONDS", "notanint")
    assert config.stale_threshold_seconds() == 3600
