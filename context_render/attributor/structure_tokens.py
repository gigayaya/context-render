"""Cross-language syntax-token classification.

A syntax-token search (`grep "def "`, `len(`, `*.py`) is structure probing — mapping code
shape, not guessing project vocabulary. classify_part routes each alternation part through
three layers (declaration-prefix strip → pure-structure glob → stoplist); a hit becomes a
`code structure` action fact, a prefix/stem remainder replaces the harvested keyword, and
a triple miss changes nothing. Prefix/stoplist contents are locked — adjust only with
corpus evidence. The stoplist deliberately excludes generic English words (`error`, `test`):
those may be real information needs, and zero false positives beats coverage.
Regex-noise stripping is duplicated from facts.canonical_key (two one-line regexes) because
facts imports this module — the dependency cannot point back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CODE_KEY = "code structure"  # fixed canonical key, parallel to facts.MAPPING_KEY

# Prefixes and stoplist locked against the corpus dry-run, 2026-07-18.
DECLARATION_PREFIXES = {
    "def", "class", "function", "func", "fn", "struct", "interface",
    "impl", "trait", "enum", "const", "import", "from", "use",
    "require", "#include", "package",
}
SYNTAX_STOPLIST = {
    "len", "self", "this", "super", "return", "import", "print",
    "init", "main", "async", "await", "yield", "lambda",
    "null", "none", "nil", "true", "false",
}

# escape classes (with a trailing quantifier: \s+ means "some whitespace"), brace
# quantifiers, anchors — replaced by a space so they still separate tokens after removal
_NOISE_RE = re.compile(r"\\[A-Za-z][*+?]?|\{\d*,?\d*\}|[\^$]")
_GLOB_SPLIT_RE = re.compile(r"[*?\[\]{}]+|/")
_FOLD_RE = re.compile(r"[^0-9a-z]")


def _fold(text: str) -> str:
    return _FOLD_RE.sub("", text.lower())


@dataclass(frozen=True)
class Classified:
    structure: bool = False     # True → code-structure action fact
    keyword: str | None = None  # non-None → harvest this text as the keyword instead
    confidence: str = "exact"   # structure=True: exact (shape) | heuristic (stoplist)


def classify_part(part: str, glob_source: bool) -> Classified:
    text = _NOISE_RE.sub(" ", part).strip()
    low = text.lower()

    for prefix in DECLARATION_PREFIXES:
        if low == prefix or low.startswith(prefix + " "):
            rest = text[len(prefix):].strip()
            if _fold(rest):
                if " " in rest:
                    # a declaration probe is `def <one identifier>`; a
                    # multi-word remainder is prose ('use --md for all') — untouched
                    return Classified()
                return Classified(keyword=rest)
            return Classified(structure=True, confidence="exact")

    # the glob layer needs an actual glob metacharacter or path separator —
    # a literal filename (`find -name ".claude.local.md"`) is an information need
    if glob_source and _GLOB_SPLIT_RE.search(text):
        stems = [s for s in _GLOB_SPLIT_RE.split(text) if s and not s.startswith(".")]
        # short all-ASCII segments (src, lib, …) are path navigation, not vocabulary —
        # a candidate threshold, reviewed against the corpus in the dry-run gate
        stems = [s for s in stems if not s.isascii() or len(_fold(s)) > 3]
        if not stems:
            return Classified(structure=True, confidence="exact")
        stem = max(stems, key=len)
        if _fold(stem) in SYNTAX_STOPLIST:
            return Classified(structure=True, confidence="heuristic")
        if stem != text:
            return Classified(keyword=stem)

    if _fold(text) in SYNTAX_STOPLIST:
        return Classified(structure=True, confidence="heuristic")
    return Classified()
