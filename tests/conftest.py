"""Pytest fixtures that isolate HOME / registry from the real user env.

Any test that reaches registry I/O with the real user ``HOME`` is a bug
— so we fail loudly at collection time if the test process's HOME still
points inside the real home (i.e. not under pytest's basetemp).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make scripts/ importable as top-level modules (registry, scanner, ...).
_SKILL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _SKILL_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Expose for tests that need to spawn subprocesses.
SKILL_ROOT = _SKILL_ROOT
SCRIPTS_DIR = _SCRIPTS
CST_PY = _SCRIPTS / "csm.py"
INSTALL_SH = _SKILL_ROOT / "install.sh"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Redirect HOME and the registry dir to tmp_path for every test.

    Also guards against a rogue test touching the real ~/.claude: if the
    registry path ends up outside tmp_path, raise before any I/O.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    reg = fake_home / ".claude" / "claude-tasks"
    reg.mkdir(parents=True)
    monkeypatch.setenv("CST_REGISTRY_DIR", str(reg))

    # Reload registry module so any cached dir paths pick up the new env.
    import importlib

    import registry as _reg  # noqa: E402

    importlib.reload(_reg)
    # Sanity: after monkeypatching, HOME must be fake_home. If something
    # ever leaks the real home into the registry dir, explode loudly.
    effective = Path(os.path.expanduser("~")).resolve()
    if effective != fake_home.resolve():
        raise RuntimeError(
            f"HOME redirect failed: expected {fake_home}, got {effective}"
        )
    if Path(os.environ["CST_REGISTRY_DIR"]).resolve() != reg.resolve():
        raise RuntimeError("CST_REGISTRY_DIR not pointing at tmp_path")

    yield
