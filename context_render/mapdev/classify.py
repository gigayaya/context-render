"""Per-line guidance classification for `ctxr map`.

Two legal signals only: markdown syntax (fences, headings, table frames) and reference
resolution against the real file tree (guidance/refs.py). Whether a kind is *good* or
*bad* is not decided here — audit applies the paper-convention reading; this module
states line-level facts.

Kinds: blank | code (fenced, delimiters included) | structural (table/rule frames) |
routing (≥1 resolvable reference) | heading (markdown heading, no reference) | prose.
A heading that resolves a reference is routing — a tree node is "path + label"
regardless of the markdown carrier syntax.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..guidance.refs import FileIndex, extract_refs

_FRAME_CHARS = set("|-:=+ ")


@dataclass(frozen=True)
class LineInfo:
    number: int            # 1-based
    kind: str
    refs: tuple[str, ...]  # resolved repo-relative targets (routing lines only)
    text: str


def classify_lines(text: str, carrier_rel: str, index: FileIndex) -> list[LineInfo]:
    out: list[LineInfo] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(LineInfo(number, "code", (), line))
            continue
        if in_fence:
            out.append(LineInfo(number, "code", (), line))
            continue
        if not stripped:
            kind, refs = "blank", ()
        elif set(stripped) <= _FRAME_CHARS:
            kind, refs = "structural", ()
        else:
            found, _ = extract_refs(line, carrier_rel, index)
            targets = tuple(sorted({r.target for r in found}))
            if targets:
                kind, refs = "routing", targets
            elif stripped.startswith("#"):
                kind, refs = "heading", ()
            else:
                kind, refs = "prose", ()
        out.append(LineInfo(number, kind, refs, line))
    return out
