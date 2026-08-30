"""Bash-mediated stale-gauge events (upgrade-plan §2.2/§2.4; design §2.2).

Read side: commands that pull a whole repo file into context (cat/head/tail/
less/more, sed -n). grep/rg hits are search fragments, not file copies — the
facts extractor owns those. Write side: targeted mutations (redirects, sed -i,
tee, mv/rm — mv/rm targeted per design §2.2 item 9, a ruled deviation from the
upgrade plan) and wildcard mutations (git commands that may rewrite arbitrary
tracked files).

Zero false positives beats coverage (AC2a): tokens containing shell
metacharacters are never harvested as paths; unparseable commands yield
nothing. Known deliberate misses: `cp`, `&>` redirects, `find -exec`, `xargs`,
scripted writes, `rm` of a directory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from .bash_heuristics import (
    GIT_GLOBAL_FLAGS_WITH_VALUE,
    SHELL_METACHARS,
    split_segments,
)

STALE_READ_CMDS = {"cat", "head", "tail", "less", "more"}
# head/tail flags whose value arrives as the NEXT token (else `head -n 50 f`
# would harvest "50" as a path)
READ_VALUE_FLAGS = {"-n", "-c"}
# git subcommands that may rewrite arbitrary tracked files (wildcard, §2.4)
GIT_WILDCARD_SUBCMDS = {"checkout", "restore", "pull", "merge", "rebase"}

_REDIR_BARE_RE = re.compile(r"^\d?>>?$")
_REDIR_ATTACHED_RE = re.compile(r"^\d?>>?(?P<t>.+)$")


def _plain_path(tok: str) -> bool:
    return bool(tok) and not (set(tok) & SHELL_METACHARS) and tok not in (".", "..")


def _abs(tok: str, cwd: str | None) -> str:
    p = Path(tok)
    if not p.is_absolute() and cwd:
        p = Path(cwd) / p
    return os.path.normpath(str(p))  # collapse `..`/`.` (Fix 3, AC2a false-positive guard)


def _has_inplace(args: list[str]) -> bool:
    return any(a == "-i" or a.startswith("-i") for a in args)


def _nonflag_args(args: list[str], value_flags: frozenset[str] | set[str] = frozenset(),
                  ) -> list[str]:
    out = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in value_flags:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def extract_stale_reads(command: str, cwd: str | None) -> list[str]:
    """Files a bash command reads wholesale into context (absolutized)."""
    paths: list[str] = []
    for seg in split_segments(command):
        if not seg:
            continue
        head = PurePosixPath(seg[0]).name
        if head in STALE_READ_CMDS:
            for tok in _nonflag_args(seg[1:], READ_VALUE_FLAGS):
                if _plain_path(tok):
                    paths.append(_abs(tok, cwd))
        elif head == "sed" and "-n" in seg[1:] and not _has_inplace(seg[1:]):
            # first non-flag arg is the script; the rest are input files
            for tok in _nonflag_args(seg[1:])[1:]:
                if _plain_path(tok):
                    paths.append(_abs(tok, cwd))
    return paths


def _git_subcmd(argv: list[str]) -> list[str] | None:
    """[subcommand, following args...] of a git segment, else None."""
    if not argv or PurePosixPath(argv[0]).name != "git":
        return None
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in GIT_GLOBAL_FLAGS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return argv[i:]
    return None


def extract_mutations(command: str, cwd: str | None,
                      ) -> tuple[list[tuple[str, str]], list[str]]:
    """(targeted mutations as (abs path, command label), wildcard labels)."""
    targets: list[tuple[str, str]] = []
    wildcards: list[str] = []
    for seg in split_segments(command):
        if not seg:
            continue
        head = PurePosixPath(seg[0]).name

        # redirects mutate their target regardless of the command
        i = 0
        while i < len(seg):
            tok = seg[i]
            target: str | None = None
            if _REDIR_BARE_RE.match(tok) and i + 1 < len(seg):
                target = seg[i + 1]
                i += 2
            else:
                m = _REDIR_ATTACHED_RE.match(tok)
                if m:
                    target = m.group("t")
                i += 1
            if target and target != "/dev/null" and _plain_path(target):
                targets.append((_abs(target, cwd), head))

        git = _git_subcmd(seg)
        if git is not None:
            sub, rest = git[0], git[1:]
            if sub in GIT_WILDCARD_SUBCMDS:
                if sub == "checkout" and any(
                        t in ("-b", "-B", "--orphan") for t in rest):
                    continue  # branch creation rewrites no tracked files
                wildcards.append(f"git {sub}")
            elif sub == "stash" and rest and rest[0] in ("pop", "apply"):
                wildcards.append(f"git stash {rest[0]}")
            continue

        if head == "sed" and _has_inplace(seg[1:]):
            for tok in _nonflag_args(seg[1:])[1:]:  # skip the script
                if _plain_path(tok):
                    targets.append((_abs(tok, cwd), "sed"))
        elif head == "tee":
            for tok in _nonflag_args(seg[1:]):
                if _plain_path(tok):
                    targets.append((_abs(tok, cwd), "tee"))
        elif head in ("mv", "rm"):
            for tok in _nonflag_args(seg[1:]):
                if _plain_path(tok):
                    targets.append((_abs(tok, cwd), head))
    return targets, wildcards
