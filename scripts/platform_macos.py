"""macOS platform detection with a test-only override hook.

The ``CST_FORCE_PLATFORM`` env var lets tests exercise the non-darwin
branches without spinning up another OS. Honored values:
``darwin``, ``linux``, and any other string (treated as non-darwin).
"""

from __future__ import annotations

import os
import sys


def current_platform() -> str:
    return os.environ.get("CST_FORCE_PLATFORM", sys.platform)


def is_macos() -> bool:
    return current_platform() == "darwin"
