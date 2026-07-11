"""token estimation (A6.2): ceil(utf8_len/4).

All outputs are marked "estimated" — files that are mostly CJK have larger error
(calibration results documented in README).
"""

from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 4)
