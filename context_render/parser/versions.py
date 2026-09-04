"""Version detection and support matrix (initial = 2.1.x)."""

from __future__ import annotations

import re

SUPPORTED_PATTERNS = [re.compile(r"^2\.1\.\d+$")]


def is_supported(version: str | None) -> bool:
    if not version:
        return False
    return any(p.match(version) for p in SUPPORTED_PATTERNS)
