"""Format-neutral path-reference extraction (guidance-reachability spec §2–§3).

Hard requirement: NO knowledge of any project's routing convention. A string in guidance
text becomes a reference edge iff it RESOLVES to a real file/dir — tables, prose, and
links are all the same thing here (same philosophy as attributor's extract_read_paths,
which never cares about command "format").

Resolution layers (spec §3):
  1. relative to the carrier's directory          → exact
  2. relative to the repo root                    → exact
  3. unique-basename match across the repo        → heuristic (ambiguous → abstain)

Stale detection is strict (dry-run finding 4): an unresolvable candidate is stale only if
it carries a known extension or its first path segment exists — bare slash idioms
(`R/L/I`, `exact/heuristic`) never qualify. Paths that exist on the real filesystem but
live in skipped dirs (runtime artifacts like `.context-render/*`) are neither edges nor
stale (spec §8).
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from ..inventory.scanner import SKIP_DIRS

PATHISH_EXT = (".py", ".md", ".yaml", ".yml", ".toml", ".json", ".sql", ".txt",
               ".sh", ".cfg", ".ini")

_FENCE_RE = re.compile(r"```.*?```|```.*\Z", re.S)
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_SPLIT_RE = re.compile(r"[\s,;()|]+")
_TRAIL_PUNCT_RE = re.compile(r"[.,:;!?]+$")
_GLOB_CHARS = ("*", "?", "[")


@dataclass(frozen=True)
class Ref:
    target: str        # repo-relative resolved path (file or dir)
    is_dir: bool
    context: str       # "inline" | "link" | "prose" | "fenced"
    raw: str
    confidence: str    # "exact" (layer 1/2) | "heuristic" (layer 3)


@dataclass(frozen=True)
class StaleRef:
    raw: str
    context: str


def _skip_dir(name: str) -> bool:
    return (name in SKIP_DIRS or name.startswith(".venv") or name.endswith(".egg-info")
            or (name.startswith(".") and name != ".claude"))


class FileIndex:
    """Repo file/dir sets + basename map (layer 3). Built once, queried many times."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files: set[str] = set()
        self.dirs: set[str] = set()
        self._basenames: dict[str, list[str]] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
            rel = os.path.relpath(dirpath, self.root)
            prefix = "" if rel == "." else rel + "/"
            for d in dirnames:
                self.dirs.add(prefix + d)
            for f in sorted(filenames):
                if f.startswith("."):  # W5 #28: dotfiles (.DS_Store, .gitignore, submodule
                    continue           # .git links) are index noise, same rule as dot-dirs
                p = prefix + f
                self.files.add(p)
                self._basenames.setdefault(f, []).append(p)

    def unique_basename(self, name: str) -> str | None:
        hits = self._basenames.get(name, [])
        return hits[0] if len(hits) == 1 else None

    def basename_exists(self, name: str) -> bool:
        return name in self._basenames


def _candidates(text: str):
    """Yield (raw, context). Fenced-block content is tagged so the graph layer can
    include/exclude it per the W5 verdict without re-extraction."""
    fenced_parts = _FENCE_RE.findall(text)
    outside = _FENCE_RE.sub(" ", text)
    for m in _LINK_RE.finditer(outside):
        yield m.group(1).strip(), "link"
    for m in _INLINE_RE.finditer(outside):
        yield m.group(1).strip(), "inline"
    stripped = _INLINE_RE.sub(" ", _LINK_RE.sub(" ", outside))
    yield from ((tok, "prose") for tok in _prose_tokens(stripped))
    for block in fenced_parts:
        body = "\n".join(block.splitlines()[1:-1])
        for m in _INLINE_RE.finditer(body):
            yield m.group(1).strip(), "fenced"
        yield from ((tok, "fenced") for tok in _prose_tokens(body))


def _prose_tokens(text: str):
    for tok in _SPLIT_RE.split(text):
        tok = tok.strip("`*\"'<>[]").rstrip("/")
        tok = _TRAIL_PUNCT_RE.sub("", tok)
        if tok and ("/" in tok or tok.endswith(PATHISH_EXT)):
            yield tok


def _clean(raw: str) -> str | None:
    raw = _TRAIL_PUNCT_RE.sub("", raw.strip().strip("`\"'"))
    if (not raw or " " in raw or raw in (".", "..")
            # leading "/" (W5 #28): absolute paths, slash-commands (/docs-drift), XML tags
            # (/svg) — never repo-relative references, never edges, never stale
            or raw.startswith(("http://", "https://", "~", "$", "-", "/"))):
        return None
    return raw.rstrip("/") or None


def _norm(base_rel: str, raw: str) -> str | None:
    joined = posixpath.normpath(posixpath.join(base_rel, raw) if base_rel != "." else raw)
    return None if joined.startswith("..") else joined


def _pathish(raw: str) -> bool:
    return "/" in raw or raw.endswith(PATHISH_EXT)


def extract_refs(text: str, carrier_rel: str, index: FileIndex) -> tuple[list[Ref], list[StaleRef]]:
    """All resolvable references in one carrier's text, plus strict stale candidates."""
    base_rel = posixpath.dirname(carrier_rel) or "."
    refs: dict[tuple[str, str], Ref] = {}
    stale: dict[tuple[str, str], StaleRef] = {}

    for cand, ctx in _candidates(text):
        raw = _clean(cand)
        if raw is None:
            continue
        resolved = _resolve(raw, base_rel, index)
        if resolved == "drop":
            continue
        if resolved:
            for target, conf in resolved:
                refs.setdefault((target, ctx),
                                Ref(target, target in index.dirs, ctx, cand.strip(), conf))
        elif _is_stale(raw, base_rel, index):
            stale.setdefault((raw, ctx), StaleRef(raw, ctx))
    return list(refs.values()), list(stale.values())


def _resolve(raw: str, base_rel: str, index: FileIndex):
    """[(target, confidence), ...] on success; [] on miss; "drop" for candidates that must
    become neither edge nor stale: ambiguous basenames (deliberate abstention) and paths
    that exist on disk but are excluded from the index (runtime artifacts, spec §8)."""
    if any(ch in raw for ch in _GLOB_CHARS):
        return _resolve_glob(raw, base_rel, index)
    for base in (base_rel, "."):
        norm = _norm(base, raw)
        if norm and (norm in index.files or norm in index.dirs):
            return [(norm, "exact")]
    if "/" not in raw and raw.endswith(PATHISH_EXT):  # layer 3: unique basename
        if index.basename_exists(raw):
            hit = index.unique_basename(raw)
            return [(hit, "heuristic")] if hit else "drop"
    for base in (base_rel, "."):
        norm = _norm(base, raw)
        if norm and (index.root / norm).exists():
            return "drop"
    return []


def _resolve_glob(raw: str, base_rel: str, index: FileIndex):
    for base in (base_rel, "."):
        root = index.root if base == "." else index.root / base
        try:
            hits = sorted(os.path.relpath(p, index.root) for p in root.glob(raw))
        except (ValueError, OSError):
            continue
        hits = [h for h in hits if h in index.files or h in index.dirs]
        if hits:
            return [(h, "exact") for h in hits]
    return []


def _is_stale(raw: str, base_rel: str, index: FileIndex) -> bool:
    if not _pathish(raw):
        return False
    if "<" in raw or ">" in raw:  # W5 #28: template placeholders (lib/<domain>/x.py)
        return False
    first = raw.split("/")[0]
    if raw.endswith(PATHISH_EXT):
        return True
    return any(_norm(b, first) in index.dirs or _norm(b, first) in index.files
               for b in (base_rel, "."))
