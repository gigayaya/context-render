"""transcript file discovery and session→repo attribution (cwd is authoritative)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_MUNGE_RE = re.compile(r"[^A-Za-z0-9]")


@dataclass
class SessionFile:
    path: Path
    session_id: str  # filename UUID
    mtime: float  # max over the main file and its sidechain files (freshness of the whole unit)
    size: int  # sum over the same set — a grown subagent file alone re-triggers ingest
    # subagent transcripts under <proj>/<session-id>/subagents/
    sidechain_paths: list[Path] = field(default_factory=list)


def _munge(path: Path) -> str:
    """Claude Code project directory name: non-alphanumeric path characters replaced with -."""
    return _MUNGE_RE.sub("-", str(path))


def _peek_cwd(path: Path, max_lines: int = 50) -> str | None:
    """Read the first few lines to get cwd (use if present)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh):
                if i >= max_lines:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = obj.get("cwd") if isinstance(obj, dict) else None
                if isinstance(cwd, str):
                    return cwd
    except OSError:
        return None
    return None


def _is_repo_project_dir(name: str, munged_prefix: str, repo_root: Path) -> bool:
    """True if the munged project-dir name is this repo root or an existing subdirectory of it.

    Munging is lossy ('/' and '-' both become '-'), so a bare prefix match also accepts
    sibling repos like /Users/x/app-api when the repo is /Users/x/app; the remainder must
    therefore munge back to a real subdirectory path.
    """
    if name == munged_prefix:
        return True
    rest = name[len(munged_prefix):] if name.startswith(munged_prefix) else None
    if not rest or not rest.startswith("-"):
        return False

    def walk(base: Path, rem: str) -> bool:
        try:
            subdirs = [e for e in base.iterdir() if e.is_dir()]
        except OSError:
            return False
        for sub in subdirs:
            m = _munge(Path(sub.name))
            if rem == m:
                return True
            if rem.startswith(m + "-") and walk(sub, rem[len(m) + 1:]):
                return True
        return False

    return walk(repo_root, rest[1:])


def _belongs(cwd: str | None, repo_root: Path) -> bool:
    if not cwd:
        return False
    try:
        Path(cwd).resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


def discover_sessions(
    repo_root: Path, projects_dir: Path | None = None
) -> list[SessionFile]:
    """Find sessions belonging to this repo. The munged directory name is only a pre-filter; cwd is authoritative.

    The CONTEXT_RENDER_PROJECTS_DIR environment variable can override the source directory (for testing).
    """
    base = (
        projects_dir
        or Path(os.environ.get("CONTEXT_RENDER_PROJECTS_DIR")
                or os.path.expanduser("~/.claude/projects"))
    )
    if not base.is_dir():
        return []
    root = repo_root.resolve()
    munged_prefix = _munge(root)
    found: list[SessionFile] = []
    for proj_dir in sorted(base.iterdir()):
        if not proj_dir.is_dir():
            continue
        # pre-filter: only dirs munging back to this repo (or a real subdirectory of it) may
        # accept cwd-less files; everything else is still verified by cwd (cwd is authoritative)
        likely = _is_repo_project_dir(proj_dir.name, munged_prefix, root)
        files = sorted(proj_dir.glob("*.jsonl"))
        if not likely:
            # A session whose cwd is textually inside the repo always lands in a `likely` dir,
            # since the dir name is munge(cwd). A non-likely dir can only belong via an aliased
            # cwd (a symlinked path that resolves into the repo) — and every session in one dir
            # shares that cwd, so the first peek that yields one settles the whole directory.
            # Without this the command opens every transcript on the machine, not just this repo's.
            peeked = (_peek_cwd(f) for f in files)  # lazy: stops at the first cwd found
            if not _belongs(next((c for c in peeked if c is not None), None), root):
                continue
        for f in files:
            cwd = _peek_cwd(f)
            if cwd is None and not likely:
                continue
            if not _belongs(cwd, root) and not (cwd is None and likely):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            mtime, size = st.st_mtime, st.st_size
            side_dir = proj_dir / f.stem / "subagents"
            side: list[Path] = []
            if side_dir.is_dir():
                for s in sorted(side_dir.glob("*.jsonl")):
                    try:
                        sst = s.stat()
                    except OSError:
                        continue
                    side.append(s)
                    mtime = max(mtime, sst.st_mtime)
                    size += sst.st_size
            found.append(
                SessionFile(path=f, session_id=f.stem, mtime=mtime, size=size,
                            sidechain_paths=side)
            )
    return found
