"""ANSI styling for the terminal renderer (pure stdlib; AC6 unaffected).

Honors NO_COLOR (https://no-color.org) and disables itself when stdout is not
a tty; CLICOLOR_FORCE=1 forces color (useful for piping into `less -R`).
Colors MUST be applied *after* padding (charts.pad_to) so escape sequences
never distort display-width alignment. The md renderer never passes a style,
so markdown output stays byte-identical to the plain form (AC3/AC9).
"""

from __future__ import annotations

import os


class Style:
    """Wraps text in ANSI codes; a disabled instance returns text unchanged."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    @classmethod
    def detect(cls) -> "Style":
        if os.environ.get("NO_COLOR") is not None:
            return cls(False)
        if os.environ.get("CLICOLOR_FORCE", "0") not in ("", "0"):
            return cls(True)
        return cls(os.isatty(1))

    def _c(self, code: str, s: str) -> str:
        if not self.enabled or not s:
            return s
        return f"\x1b[{code}m{s}\x1b[0m"

    def bold(self, s: str) -> str:
        return self._c("1", s)

    def dim(self, s: str) -> str:
        return self._c("2", s)

    def red(self, s: str) -> str:
        return self._c("31", s)

    def green(self, s: str) -> str:
        return self._c("32", s)

    def yellow(self, s: str) -> str:
        return self._c("33", s)

    def blue(self, s: str) -> str:
        return self._c("34", s)

    def magenta(self, s: str) -> str:
        return self._c("35", s)

    def cyan(self, s: str) -> str:
        return self._c("36", s)
