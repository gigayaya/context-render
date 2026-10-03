"""Routing-map skeleton generation (`ctxr map init`).

Deterministic structure only — paths + TODO labels; the semantic half (what rule a file
governs, single-authority status) is delegated to the user's agent via the fill
instructions, keeping the zero-API-call constraint intact. Shape follows the paper's
working rule: flat up to FLAT_THRESHOLD files, grouped-by-top-level-directory beyond.
The generated skeleton must itself pass `ctxr map` clean (self-consistency, tested).
"""

from __future__ import annotations

import posixpath

from ..guidance.refs import FileIndex

FLAT_THRESHOLD = 300  # paper working rule: "up to a few hundred files, go flat"

_TITLE = "# Repository routing map"
_ENTRY = "- `{path}` — TODO: one-line label"
_GROUP = "## `{d}/` — TODO: one-line label"


def build_skeleton(index: FileIndex, shape: str = "auto") -> str:
    files = sorted(f for f in index.files if posixpath.basename(f) != "CLAUDE.md")
    if shape == "auto":
        shape = "flat" if len(files) <= FLAT_THRESHOLD else "tree"

    out = [_TITLE, ""]
    if shape == "flat":
        out += [_ENTRY.format(path=f) for f in files]
    else:
        out += [_ENTRY.format(path=f) for f in files if "/" not in f]
        groups: dict[str, list[str]] = {}
        for f in files:
            if "/" in f:
                groups.setdefault(f.split("/", 1)[0], []).append(f)
        for d in sorted(groups):
            out += ["", _GROUP.format(d=d), ""]
            out += [_ENTRY.format(path=f) for f in groups[d]]
    return "\n".join(out).rstrip("\n") + "\n"


def fill_instructions() -> str:
    return """\
# Routing-map fill instructions

Complete the routing map by replacing every "TODO: one-line label".

For each entry:
- State which rule or concern the file governs, and whether it is the single authority
  for it — one line, nothing more.
- Say what the path cannot: never restate the file's name, directory, or type.
- Never add architectural prose anywhere in the map — any sentence that is neither a
  path nor its one-line label belongs in docs/, not in guidance.
- If a file is not worth routing to, delete its entry rather than leaving the TODO.

When done, run `ctxr map` to verify the result: prose share 0, no dead routes,
no label echoes.
"""
