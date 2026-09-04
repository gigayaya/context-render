"""Stale-copy window extraction.

Walks a parsed session once and, per context window (main chain and each
subagent) per repo file, tracks whether a copy read into the
window was later overturned by a mutation without being re-read — the
"read → mutated → never re-read" accident site. Gauge, not grader:
a stale window is a state, never a verdict.

Extraction happens at ingest because transcripts expire (~30 days); rows land
in the stale_windows table. Confidence discipline: exact = Read/Edit/Write/
MultiEdit/NotebookEdit tool events, heuristic = bash-mediated events,
wildcard mutations, or cross-window triggers; never promoted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..inventory.tokens import estimate_tokens
from ..parser.loader import Event, ParsedSession, index_tool_results
from .bash_mutations import extract_mutations, extract_stale_reads

# Version of the extraction rules (not the DB schema). Bump on any rule change;
# sync re-extracts sessions whose _stale:extract marker carries an older number.
STALE_EXTRACTOR_VERSION = 1

MUTATION_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}

_Window = tuple[bool, str | None]  # (is_sidechain, agent) — window identity


@dataclass
class StaleWindow:
    path: str            # repo-relative
    window_side: bool    # sidechain flag
    window_agent: str    # sidechain identity; '' = main chain
    read_idx: int
    mutate_idx: int
    mutate_tool: str     # Edit | MultiEdit | Write | NotebookEdit | bash command head
    close_idx: int | None  # None = never re-read (closed by session end)
    outcome: str         # re-read | compacted | never-re-read
    read_tokens_est: int
    read_partial: bool   # opening read carried offset/limit (archived, not judged)
    confidence: str      # exact | heuristic


@dataclass
class _Copy:  # IN-CONTEXT state of one (window, path)
    read_idx: int
    read_tokens_est: int
    read_partial: bool
    read_conf: str


@dataclass
class _Open:  # STALE-OPEN state
    copy: _Copy
    mutate_idx: int
    mutate_tool: str
    confidence: str


def extract_stale(parsed: ParsedSession,
                  repo_root: Path | None = None) -> list[StaleWindow]:
    """repo_root: ingest-time repo root; paths outside it are never tracked
    (prefer a miss, AC2a). None (tests) keeps paths as recorded."""
    root = str(repo_root).rstrip("/") if repo_root is not None else None

    results = index_tool_results(parsed.events)

    def result_of(ev: Event) -> Event | None:
        r = results.get(ev.tool_use_id or "")
        return r if r is not None and not r.is_error else None

    in_context: dict[tuple[_Window, str], _Copy] = {}
    stale_open: dict[tuple[_Window, str], _Open] = {}
    out: list[StaleWindow] = []

    def _rel(path: str, cwd: str | None) -> str | None:
        p = path
        if not p.startswith("/") and cwd:
            p = str(Path(cwd) / p)
        p = os.path.normpath(p)  # collapse `..`/`.` so both forms unify (AC2a)
        if root is None:
            return p
        if p.startswith(root + "/"):
            return p[len(root) + 1:]
        return None  # outside the repo: never tracked

    def close(key: tuple[_Window, str], close_idx: int | None, outcome: str) -> None:
        o = stale_open.pop(key)
        win, path = key
        out.append(StaleWindow(
            path=path, window_side=win[0], window_agent=win[1] or "",
            read_idx=o.copy.read_idx, mutate_idx=o.mutate_idx,
            mutate_tool=o.mutate_tool, close_idx=close_idx, outcome=outcome,
            read_tokens_est=o.copy.read_tokens_est,
            read_partial=o.copy.read_partial, confidence=o.confidence))

    def record_read(win: _Window, rel: str, idx: int, tokens: int,
                    partial: bool, conf: str) -> None:
        key = (win, rel)
        if key in stale_open:
            close(key, idx, "re-read")
        in_context[key] = _Copy(read_idx=idx, read_tokens_est=tokens,
                                read_partial=partial, read_conf=conf)

    def record_mutation(mut_win: _Window, rel: str | None, idx: int, tool: str,
                        conf: str, wildcard: bool = False) -> None:
        # a mutation changes repo state (session-global): broadcast to every
        # window holding an IN-CONTEXT copy; wildcard hits every held path
        for key in list(in_context):
            win, path = key
            if not wildcard and path != rel:
                continue
            copy = in_context.pop(key)
            wconf = conf
            if copy.read_conf == "heuristic" or win != mut_win or wildcard:
                wconf = "heuristic"
            if key not in stale_open:  # first trigger holds
                stale_open[key] = _Open(copy=copy, mutate_idx=idx,
                                        mutate_tool=tool, confidence=wconf)

    for ev in parsed.events:
        win: _Window = (ev.is_sidechain, ev.agent)
        if ev.kind == "compaction":
            # the window's copies leave with the rewrite: close open windows as
            # compacted, reset IN-CONTEXT to CLEAN
            for key in [k for k in stale_open if k[0] == win]:
                close(key, ev.idx, "compacted")
            for key in [k for k in in_context if k[0] == win]:
                del in_context[key]
            continue
        if ev.kind != "tool_use":
            continue
        result = result_of(ev)
        if result is None:
            continue
        tin = ev.tool_input or {}
        tool = ev.tool_name or ""

        if tool == "Read":
            target = tin.get("file_path")
            if not isinstance(target, str):
                continue
            rel = _rel(target, ev.cwd)
            if rel is None:
                continue
            partial = tin.get("offset") is not None or tin.get("limit") is not None
            record_read(win, rel, ev.idx, estimate_tokens(result.text or ""),
                        partial, "exact")
        elif tool in MUTATION_TOOLS:
            target = tin.get("file_path") or tin.get("notebook_path")
            if not isinstance(target, str):
                continue
            rel = _rel(target, ev.cwd)
            if rel is None:
                continue
            record_mutation(win, rel, ev.idx, tool, "exact")
        elif tool == "Bash":
            cmd = tin.get("command")
            if not isinstance(cmd, str):
                continue
            read_paths = [r for r in (
                _rel(p, ev.cwd) for p in extract_stale_reads(cmd, ev.cwd))
                if r is not None]
            per = (estimate_tokens(result.text or "") // len(read_paths)
                   if read_paths else 0)
            for rel in read_paths:
                record_read(win, rel, ev.idx, per, False, "heuristic")
            targets, wildcards = extract_mutations(cmd, ev.cwd)
            for p, label in targets:
                rel = _rel(p, ev.cwd)
                if rel is not None:
                    record_mutation(win, rel, ev.idx, label, "heuristic")
            for label in wildcards:
                record_mutation(win, None, ev.idx, label, "heuristic",
                                wildcard=True)

    for key in sorted(stale_open, key=lambda k: stale_open[k].mutate_idx):
        close(key, None, "never-re-read")
    out.sort(key=lambda w: (w.mutate_idx, w.read_idx, w.path))
    return out
