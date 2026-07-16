"""Bash-mediated event heuristics (A7.6).

Zero tolerance for false positives takes priority over false-negative coverage (AC2a).
Known false negatives (documented in README): redirection `<`, `find -exec`, `xargs`,
`sed`/`awk`, and file reads mediated indirectly by scripts/interpreters.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

# shlex punctuation mode emits runs of these as standalone tokens; an unquoted newline
# separates commands exactly like `;` (multi-line Bash is the norm in transcripts)
SEPARATOR_CHARS = set("|&;\n")
READ_CMDS = {"cat", "head", "tail", "less", "more", "grep", "rg"}
# A token containing these characters cannot be a plain path (redirect/substitution/glob) → not a candidate (zero false positives)
SHELL_METACHARS = set("<>|&$(){}`*?")
# git global flags (that take a value), skipped when determining the subcommand
GIT_GLOBAL_FLAGS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
# grep/rg flags whose value arrives as the NEXT token: the value must be consumed, or it
# occupies the pattern slot and the real pattern gets harvested as a path — patterns are
# often existing file names (`grep -A 3 CLAUDE.md notes.txt`), a false positive (AC2a).
# Inline forms (-A3, --context=3) stay inside the flag token and need no handling.
GREP_RG_VALUE_FLAGS = {
    "-A", "-B", "-C", "-m", "-e", "-f", "-d", "-D",
    "--after-context", "--before-context", "--context", "--max-count",
    "--regexp", "--file", "--include", "--exclude", "--exclude-from",
    "--exclude-dir", "--label", "--binary-files", "--directories", "--devices",
    # rg-only
    "-g", "-t", "-T", "-E", "-M", "-j", "-r",
    "--glob", "--iglob", "--type", "--type-not", "--type-add", "--encoding",
    "--engine", "--max-depth", "--max-filesize", "--max-columns", "--threads",
    "--replace", "--sort", "--sortr", "--colors", "--pre", "--ignore-file",
    "--context-separator",
}
# pattern supplied via flag → every positional argument is a file (POSIX grep semantics)
GREP_RG_PATTERN_FLAGS = {"-e", "-f", "--regexp", "--file"}

COMMIT_RESULT_RE = re.compile(r"\[[^\]]+ [0-9a-f]{7,}\]|files? changed")


def split_segments_seps(command: str) -> list[tuple[str, list[str]]]:
    """Like split_segments, but each segment keeps the separator run that preceded it
    ("" for the first) — the facts extractor needs to know whether a grep sits mid-pipeline.
    Unparseable (unbalanced quotes, etc.) → empty list (prefer misses over false positives).

    Punctuation mode emits separators as their own tokens even when attached
    (`2>/dev/null; ls` → `2>/dev/null`, `;`, `ls`), and a quoted newline stays inside
    its token (`-m "a\\nb"` is one argument, not a boundary).
    """
    lex = shlex.shlex(command, posix=True, punctuation_chars="|&;\n")
    lex.whitespace = " \t\r"  # an unquoted \n is a segment boundary, not plain whitespace
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return []
    segments: list[tuple[str, list[str]]] = []
    cur: list[str] = []
    sep = ""
    for tok in tokens:
        # runs of punctuation arrive as one token ("&&", ";", "&&\n") → all boundaries
        if not set(tok) - SEPARATOR_CHARS:
            if cur:
                segments.append((sep, cur))
                cur = []
            sep = tok
        else:
            cur.append(tok)
    if cur:
        segments.append((sep, cur))
    return segments


def is_pipe_sep(sep: str) -> bool:
    """True when the separator run feeds the previous segment's stdout into the next
    (`|`, `|&`, possibly wrapped in unquoted newlines); `||` and `&&` are not pipes."""
    return sep.strip("\n") in ("|", "|&")


def split_segments(command: str) -> list[list[str]]:
    """Tokenize with shlex in punctuation mode, then split into segments on | & ; and
    unquoted newlines. Unparseable (unbalanced quotes, etc.) → empty list (prefer misses
    over false positives).
    """
    return [argv for _, argv in split_segments_seps(command)]


def _is_git_commit_segment(argv: list[str]) -> bool:
    if not argv or PurePosixPath(argv[0]).name != "git":
        return False
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in GIT_GLOBAL_FLAGS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok == "commit"
    return False


def detect_git_commit(command: str, result_text: str | None) -> bool:
    """In-segment git commit candidate + tool_result double confirmation (A7.6)."""
    if not any(_is_git_commit_segment(seg) for seg in split_segments(command)):
        return False
    if result_text is None:
        return False
    return bool(COMMIT_RESULT_RE.search(result_text))


def extract_read_paths(command: str, cwd: str | None) -> list[str]:
    """File-read heuristic: non-flag arguments of cat/head/tail/less/more/grep/rg → candidate paths (absolutized).

    grep/rg skip the first non-flag argument (= pattern).
    """
    paths: list[str] = []
    for seg in split_segments(command):
        if not seg:
            continue
        cmd = PurePosixPath(seg[0]).name
        if cmd not in READ_CMDS:
            continue
        is_grep = cmd in {"grep", "rg"}
        skip_first_nonflag = is_grep
        seen_nonflag = 0
        i = 1
        while i < len(seg):
            tok = seg[i]
            if tok.startswith("-"):
                if is_grep and tok in GREP_RG_VALUE_FLAGS:
                    if tok in GREP_RG_PATTERN_FLAGS:
                        skip_first_nonflag = False
                    i += 2  # consume the flag and its separate value
                    continue
                i += 1
                continue
            if any(ch in SHELL_METACHARS for ch in tok) or tok in (".", ".."):
                i += 1
                continue
            seen_nonflag += 1
            if skip_first_nonflag and seen_nonflag == 1:
                i += 1
                continue
            p = Path(tok)
            if not p.is_absolute() and cwd:
                p = Path(cwd) / p
            paths.append(str(p))
            i += 1
    return paths
