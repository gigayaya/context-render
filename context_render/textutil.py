"""Control-sequence sanitation for transcript-derived display text.

Transcript content (user prompts, commands, paths, tool output) is untrusted display
input: raw ESC/C1 bytes would let a session rewrite the terminal it is being reviewed
in (or retitle the window). Cleaned at the point of capture (attributor/parser), so DB
evidence and written reports stay clean too. Tabs and newlines are layout concerns,
not control-sequence risks — they pass through unchanged.
"""

from __future__ import annotations

import re

# well-formed CSI (color/cursor) and OSC (title-set) sequences vanish whole, payload included
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
# stray C0 controls (minus \t \n), DEL, and C1 (0x9b is a one-byte CSI)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")


def clean(text: str) -> str:
    return _CTRL_RE.sub("", _ANSI_RE.sub("", text))
