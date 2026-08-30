"""Self-derivation fact extraction (SPIKES.md W3).

Every agent search is a question the harness didn't answer. This module walks a parsed
session and records those questions as facts: keyword searches (Grep/Glob tool calls and
bash search commands — W3 #20: both channels), structure-mapping actions (`repo layout`,
W3 #18), and chain reads (a Read sandwiched between searches whose path came out of the
previous search's results).

Extraction happens at ingest: transcripts expire (~30 days) and these event-level signals
exist nowhere else once they do. Decontamination rules are frozen W3 #19 verdicts —
`grep -v` segments, mid-pipeline filter greps, and `find -path`/`-prune` arguments never
produce keywords. Confidence discipline applies: exact = observed marker, heuristic =
best-effort inference, never promoted without transcript evidence.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..inventory.tokens import estimate_tokens
from ..parser.loader import Event, ParsedSession
from ..textutil import clean
from .bash_heuristics import (
    GREP_RG_VALUE_FLAGS,
    GREP_VALUE_FLAGS,
    GIT_GLOBAL_FLAGS_WITH_VALUE,
    is_pipe_sep,
    split_segments_seps,
)
from .structure_tokens import CODE_KEY, classify_part

MAPPING_KEY = "repo layout"  # fixed canonical key of the v1 action-row detector

# Version of the extraction rules themselves (not the DB schema). Bump on any rule change;
# sync re-extracts sessions whose marker carries an older number (missing field = 1 = W3).
# v3: chain_read raw is stored repo-relative (the joinable form — coverage matches it
# against the FileIndex directly; v2 stored absolute paths, which no consumer could map).
FACTS_EXTRACTOR_VERSION = 3

GREP_FAMILY = {"grep", "egrep", "fgrep", "rg", "ag"}
# grep/ag vs rg flag sets (GREP_VALUE_FLAGS / GREP_RG_VALUE_FLAGS) come from
# bash_heuristics: rg-only flags like -r (--replace) mean something flagless for POSIX
# grep (-r = recursive) — consuming their "value" would eat the real pattern.
# fd flags whose value arrives as the next token (subset; unknown flags just skip themselves)
FD_VALUE_FLAGS = {
    "-e", "-E", "-t", "-d", "-j", "-S", "-o",
    "--extension", "--exclude", "--type", "--max-depth", "--min-depth",
    "--search-path", "--threads", "--size", "--owner", "--base-directory",
}
# find predicates that take one value we must consume but never harvest
FIND_VALUE_PREDICATES = {
    "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
    "-maxdepth", "-mindepth", "-size", "-mtime", "-mmin", "-newer", "-user",
    "-group", "-perm", "-printf", "-fprintf", "-exec", "-execdir", "-ok",
}

_RAW_CAP = 200  # same cap as attribution evidence summaries


@dataclass
class Fact:
    idx: int
    kind: str  # search | mapping | chain_read
    key: str  # canonical key; mapping is always MAPPING_KEY
    raw: str  # original pattern (search) / command segment (mapping) / read path
    #          (chain_read: repo-relative when under the ingest-time repo root)
    tool: str | None  # Grep | Glob | Read | Bash (mapping) | bash search head (grep, rg, …)
    tokens_est: int = 0
    occupancy_est: int | None = None  # tokens × remaining assistant turns; None = not computable
    sidechain: bool = False
    confidence: str = "exact"
    agent: str | None = None  # sidechain window identity; not persisted (facts table has no column)


@dataclass
class FactExtraction:
    facts: list[Fact] = field(default_factory=list)
    # Σ ceil(utf8/4) over every tool_result in the session — the "% of tool output"
    # denominator for the analyze summary line
    tool_output_tokens_est: int = 0


# ---- canonical normalization (§4.1) ----

_ESCAPE_CLASS_RE = re.compile(r"\\[A-Za-z]")  # \b \s \w \d … regex escape classes
_BRACE_QUANT_RE = re.compile(r"\{\d*,?\d*\}")
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z一-鿿]")  # keep alnum + CJK 一-鿿


def split_alternation(raw: str) -> list[str]:
    """`a\\|b` / `a|b` → candidate parts (tokens are split evenly by the caller)."""
    parts = [p for p in raw.replace("\\|", "|").split("|") if p.strip()]
    return parts or [raw]


def canonical_key(part: str) -> str:
    """Regex noise removed, case folded, non-word (non-alnum/CJK) folded away;
    a fold shorter than 3 falls back to the original text strip+lower."""
    t = _ESCAPE_CLASS_RE.sub("", part)
    t = _BRACE_QUANT_RE.sub("", t)
    folded = _NON_WORD_RE.sub("", t).lower()
    if len(folded) < 3:
        return part.strip().lower()
    return folded


# ---- bash segment analysis ----

def _head(argv: list[str]) -> str:
    return PurePosixPath(argv[0]).name if argv else ""


def _grep_argv(argv: list[str]) -> tuple[str, list[str]] | None:
    """(head label, args) for a grep-family or `git grep` segment, else None."""
    head = _head(argv)
    if head in GREP_FAMILY:
        return head, argv[1:]
    if head == "git":
        i = 1
        while i < len(argv):
            tok = argv[i]
            if tok in GIT_GLOBAL_FLAGS_WITH_VALUE:
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            if tok == "grep":
                return "git grep", argv[i + 1:]
            return None
    return None


def _grep_pattern(head: str, args: list[str], piped: bool) -> str | None:
    """Pattern of a grep-family segment, after the W3 #19 decontamination gates:
    `-v` segments are exclusions, and a mid-pipeline grep with no path arguments and
    no recursive flag is a filter — neither is a search."""
    value_flags = GREP_RG_VALUE_FLAGS if head == "rg" else GREP_VALUE_FLAGS
    pattern: str | None = None
    paths: list[str] = []
    recursive = False
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("--"):
            if tok == "--invert-match":
                return None
            if tok in ("--recursive",) or tok.startswith("--include"):
                recursive = True
            if tok in ("--file",):
                return None  # patterns live in a file the transcript doesn't show
            if tok == "--regexp" and i + 1 < len(args):
                pattern = pattern or args[i + 1]
                i += 2
                continue
            if tok in value_flags:
                i += 2
                continue
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            cluster = tok[1:]
            if tok in value_flags:
                if tok == "-f":
                    return None
                if tok == "-e" and i + 1 < len(args):
                    pattern = pattern or args[i + 1]
                i += 2
                continue
            # short-flag cluster (-rn, -ril …): value flags never cluster with these
            if "v" in cluster:
                return None
            if "r" in cluster or "R" in cluster:
                recursive = True
            i += 1
            continue
        if pattern is None:
            pattern = tok
        else:
            paths.append(tok)
        i += 1
    if pattern is None:
        return None
    if piped and not paths and not recursive:
        return None  # pipeline filter, not a search
    return pattern


def _fd_pattern(args: list[str]) -> str | None:
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ("-x", "--exec", "-X", "--exec-batch"):
            return None  # everything after is a command, and -exec use is action, not search
        if tok in FD_VALUE_FLAGS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok
    return None


@dataclass
class _FindScan:
    names: list[str] = field(default_factory=list)
    has_type_d: bool = False
    has_prune: bool = False


def _scan_find(args: list[str]) -> _FindScan:
    out = _FindScan()
    negated = False  # -not / ! negates the next primary — a negated -name is exclusion
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ("-not", "!"):
            negated = True
            i += 1
            continue
        if tok in ("-name", "-iname") and i + 1 < len(args):
            if not negated:
                out.names.append(args[i + 1])
            negated = False
            i += 2
            continue
        if tok == "-prune":
            out.has_prune = True
            negated = False
            i += 1
            continue
        if tok == "-type" and i + 1 < len(args):
            if args[i + 1] == "d" and not negated:
                out.has_type_d = True
            negated = False
            i += 2
            continue
        if tok in FIND_VALUE_PREDICATES:
            negated = False
            i += 2
            continue
        if tok not in ("(", ")"):
            negated = False  # any other primary consumes the pending -not
        i += 1
    if out.has_prune:
        # a -prune segment is exclusion machinery (W3 #19): its -name values are the
        # things being cut out, never what the agent was after
        out.names = []
    return out


def _seg_raw(argv: list[str]) -> str:
    return clean(shlex.join(argv))[:_RAW_CAP]


def _part_raw(part: str) -> str:
    """raw of one search candidate: the alternation part itself, so a\\|b lands as two
    rows readable on their own (the full original stays reachable via the evidence idx)."""
    return clean(part.strip())[:_RAW_CAP]


def _classify(part: str, glob_source: bool) -> tuple[str, str, str, str]:
    """(kind, key, raw, confidence) for one alternation part, post-classification."""
    c = classify_part(part, glob_source)
    if c.structure:
        return "mapping", CODE_KEY, _part_raw(part), c.confidence
    if c.keyword is not None:
        return "search", canonical_key(c.keyword), _part_raw(part), "exact"
    return "search", canonical_key(part), _part_raw(part), "exact"


@dataclass
class _CallFacts:
    """Facts found inside one Bash call, before result tokens are split evenly."""

    searches: list[tuple[str, str, str, str]] = field(default_factory=list)  # (tool, key, raw, conf)
    mappings: list[tuple[str, str, str]] = field(default_factory=list)  # (key, raw, confidence)
    ls_segments: list[list[str]] = field(default_factory=list)
    ls_only: bool = False  # every segment of the call is an ls


def _classify_into(out: _CallFacts, pattern: str, label: str, glob_source: bool) -> None:
    for part in split_alternation(pattern):
        kind, key, raw, conf = _classify(part, glob_source)
        if kind == "mapping":
            out.mappings.append((key, raw, conf))
        else:
            out.searches.append((label, key, raw, conf))


def _analyze_bash(command: str) -> _CallFacts:
    out = _CallFacts()
    segs = split_segments_seps(command)
    non_ls = 0
    for sep, argv in segs:
        if not argv:
            continue
        head = _head(argv)
        if head == "ls":
            out.ls_segments.append(argv)
            continue
        non_ls += 1
        grep = _grep_argv(argv)
        if grep is not None:
            label, args = grep
            pattern = _grep_pattern(label, args, piped=is_pipe_sep(sep))
            if pattern:
                _classify_into(out, pattern, label, glob_source=False)
            continue
        if head == "fd":
            pattern = _fd_pattern(argv[1:])
            if pattern:
                _classify_into(out, pattern, "fd", glob_source=True)
            continue
        if head == "find":
            scan = _scan_find(argv[1:])
            for name in scan.names:
                _classify_into(out, name, "find", glob_source=True)
            if scan.has_type_d and not scan.names:
                out.mappings.append((MAPPING_KEY, _seg_raw(argv), "exact"))
            continue
        if head == "tree":
            out.mappings.append((MAPPING_KEY, _seg_raw(argv), "exact"))
            continue
    if len(out.ls_segments) >= 2:
        # ≥2 ls segments in one call is itself a mapping ritual (heuristic)
        out.mappings.append(
            (MAPPING_KEY,
             "; ".join(_seg_raw(a) for a in out.ls_segments)[:_RAW_CAP], "heuristic"))
        out.ls_segments = []
    out.ls_only = bool(segs) and non_ls == 0 and not out.mappings
    return out


# ---- extraction over the event stream ----

def _window(ev: Event) -> tuple[bool, str | None]:
    """Context-window identity (W2 window-separation): main chain vs each subagent."""
    return (ev.is_sidechain, ev.agent)


def extract_facts(parsed: ParsedSession,
                  repo_root: Path | None = None) -> FactExtraction:
    """repo_root: the ingest-time repo root; chain_read raws under it are stored
    repo-relative (v3) so coverage can join them against the FileIndex without any
    per-session path bookkeeping. None (tests, unknown root) keeps paths as recorded."""
    ext = FactExtraction()
    root = str(repo_root).rstrip("/") if repo_root is not None else None

    results: dict[str, Event] = {}
    for ev in parsed.events:
        if ev.kind == "tool_result" and ev.tool_use_id:
            if ev.tool_use_id not in results:
                results[ev.tool_use_id] = ev
            ext.tool_output_tokens_est += estimate_tokens(ev.text or "")

    def result_of(ev: Event) -> Event | None:
        r = results.get(ev.tool_use_id or "")
        return r if r is not None and not r.is_error else None

    raw_facts: list[Fact] = []
    # per-window trailing run of ls-only Bash calls: (idx, tokens, sidechain)
    ls_runs: dict[tuple[bool, str | None], list[tuple[int, int]]] = {}
    # per-window last search action: (idx, result_text, primary key) — chain_read anchor
    last_search: dict[tuple[bool, str | None], tuple[int, str, str]] = {}
    # per-window pending chain-read candidates awaiting the closing search
    pending_reads: dict[tuple[bool, str | None], list[Fact]] = {}

    def add(fact: Fact) -> None:
        raw_facts.append(fact)

    def flush_ls(win: tuple[bool, str | None]) -> None:
        run = ls_runs.pop(win, [])
        if len(run) >= 2:  # ≥2 consecutive ls-bodied tool calls → mapping (heuristic)
            add(Fact(idx=run[0][0], kind="mapping", key=MAPPING_KEY, raw="ls chain",
                     tool="Bash", tokens_est=sum(t for _, t in run),
                     sidechain=win[0], confidence="heuristic", agent=win[1]))

    def close_search(win: tuple[bool, str | None], idx: int, text: str, key: str) -> None:
        # a later search closes the sandwich: pending reads between two searches land
        for f in pending_reads.pop(win, []):
            add(f)
        last_search[win] = (idx, text, key)

    for ev in parsed.events:
        if ev.kind != "tool_use":
            continue
        win = _window(ev)
        tool = ev.tool_name or ""
        tin = ev.tool_input or {}
        result = result_of(ev)
        result_tokens = estimate_tokens(result.text or "") if result is not None else 0

        if tool in ("Grep", "Glob"):
            flush_ls(win)
            pattern = tin.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                continue
            parts = split_alternation(pattern)
            per = result_tokens // len(parts)
            classified = [_classify(part, tool == "Glob") for part in parts]
            for kind, key, raw, conf in classified:
                add(Fact(idx=ev.idx, kind=kind, key=key, raw=raw, tool=tool,
                         tokens_est=per, sidechain=ev.is_sidechain, confidence=conf,
                         agent=ev.agent))
            search_keys = [key for kind, key, _, _ in classified if kind == "search"]
            close_search(win, ev.idx, (result.text or "") if result else "",
                         search_keys[0] if search_keys else CODE_KEY)
            continue

        if tool == "Read":
            flush_ls(win)  # a Read between ls calls breaks the "consecutive" chain
            # chain_read candidate (§4.3, both conditions): path came out of the previous
            # search's results AND a later search follows — recorded when one does
            target = tin.get("file_path")
            prev = last_search.get(win)
            if (isinstance(target, str) and prev is not None and result is not None
                    and _path_in_text(target, prev[1], ev.cwd)):
                pending_reads.setdefault(win, []).append(
                    Fact(idx=ev.idx, kind="chain_read", key=prev[2],
                         raw=clean(_repo_rel(target, root))[:_RAW_CAP], tool="Read",
                         tokens_est=result_tokens, sidechain=ev.is_sidechain,
                         confidence="heuristic", agent=ev.agent)
                )
            continue

        if tool != "Bash":
            flush_ls(win)
            continue

        cmd = tin.get("command")
        if not isinstance(cmd, str):
            continue
        call = _analyze_bash(cmd)

        if call.ls_only:
            ls_runs.setdefault(win, []).append((ev.idx, result_tokens))
            continue
        flush_ls(win)

        n_facts = len(call.searches) + len(call.mappings)
        if not n_facts:
            continue
        per = result_tokens // n_facts
        for label, key, raw, conf in call.searches:
            add(Fact(idx=ev.idx, kind="search", key=key, raw=raw, tool=label,
                     tokens_est=per, sidechain=ev.is_sidechain, confidence=conf,
                     agent=ev.agent))
        for key, raw, conf in call.mappings:
            add(Fact(idx=ev.idx, kind="mapping", key=key, raw=raw, tool="Bash",
                     tokens_est=per, sidechain=ev.is_sidechain, confidence=conf,
                     agent=ev.agent))
        if call.searches or any(k == CODE_KEY for k, _, _ in call.mappings):
            anchor = call.searches[0][1] if call.searches else CODE_KEY
            close_search(win, ev.idx, (result.text or "") if result else "", anchor)

    for win in list(ls_runs):
        flush_ls(win)
    # pending reads whose closing search never came are dropped (not sandwiched)

    _merge_duplicates(raw_facts)
    _attach_occupancy(raw_facts, parsed.events)
    ext.facts = raw_facts
    return ext


def _repo_rel(target: str, root: str | None) -> str:
    """chain_read raw normalization: repo-relative when under the ingest-time repo root,
    otherwise unchanged (an outside-repo read can never join — prefer a miss, AC2a)."""
    if root and target.startswith(root + "/"):
        return target[len(root) + 1:]
    return target


def _path_in_text(target: str, text: str, cwd: str | None) -> bool:
    if not text:
        return False
    if target in text:
        return True
    if cwd and target.startswith(cwd.rstrip("/") + "/"):
        return target[len(cwd.rstrip("/")) + 1:] in text
    return False


def _merge_duplicates(facts: list[Fact]) -> None:
    """(idx, kind, key) is part of the primary key — same-call duplicates (e.g. two
    alternation parts folding to one canonical) merge, summing tokens."""
    seen: dict[tuple[int, str, str], Fact] = {}
    merged: list[Fact] = []
    for f in facts:
        k = (f.idx, f.kind, f.key)
        prev = seen.get(k)
        if prev is None:
            seen[k] = f
            merged.append(f)
        else:
            prev.tokens_est += f.tokens_est
            if f.confidence == "heuristic":
                prev.confidence = "heuristic"
    facts[:] = merged


def _attach_occupancy(facts: list[Fact], events: list[Event]) -> None:
    """occupancy_est = tokens × assistant turns remaining after the event, cut at the
    next compaction boundary — per context window (W2 separation: a sidechain fact
    counts turns in its own file's stream). Windows with no assistant turns at all
    can't be measured → None, never guessed."""
    assistants: dict[tuple[bool, str | None], list[int]] = {}
    compactions: dict[tuple[bool, str | None], list[int]] = {}
    for ev in events:
        if ev.kind == "assistant_msg":
            assistants.setdefault(_window(ev), []).append(ev.idx)
        elif ev.kind == "compaction":
            compactions.setdefault(_window(ev), []).append(ev.idx)
    for f in facts:
        win = (f.sidechain, f.agent)
        win_assistants = assistants.get(win)
        if win_assistants is None:
            f.occupancy_est = None
            continue
        bound = next((c for c in compactions.get(win, []) if c > f.idx), None)
        remaining = sum(1 for a in win_assistants
                        if a > f.idx and (bound is None or a < bound))
        f.occupancy_est = f.tokens_est * remaining
