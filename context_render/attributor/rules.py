"""Three-state attribution rules (A7).

Output: session-level (component_id, state, count, confidence, evidence[])
    + event-level (ts, component, state transition) triples (for the timeline).
confidence ∈ {exact, heuristic} — trust comes from being auditable (drill into
evidence), not from claiming zero errors.
State monotonicity: take the highest state within a session, no regression
(compaction does not lower the state).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..inventory.scanner import Component
from ..inventory.tokens import estimate_tokens
from ..parser.loader import Event, ParsedSession, SKILL_BASE_DIR_PREFIX
from ..textutil import clean
from . import bash_heuristics

COMMAND_MARKER_RE = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
MCP_TOOL_RE = re.compile(r"^mcp__([^_]+(?:_[^_]+)*?)__(.+)$")

# tools whose tool_input names an edited file (file_path / notebook_path)
FILE_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _is_boilerplate(text: str) -> bool:
    """system-generated user_msg text (markers, caveat preamble) — not a real prompt."""
    return text.startswith("<") or text.startswith("Caveat:")


def _tag(ev: Event, text: str) -> str:
    """Sidechain evidence/detail names its subagent, so drill-down shows which window acted.

    Every evidence/detail string passes through here → single choke point to strip
    terminal control sequences from transcript-derived text before it is stored or shown."""
    text = clean(text)
    if not ev.is_sidechain:
        return text
    return f"[subagent:{ev.agent}] {text}" if ev.agent else f"[subagent] {text}"


@dataclass
class UsageAgg:
    count: int = 0
    confidence: str = "exact"  # any heuristic evidence → heuristic
    evidence: list[dict] = field(default_factory=list)

    def add(self, ev: Event, summary: str, confidence: str) -> None:
        self.count += 1
        if confidence == "heuristic":
            self.confidence = "heuristic"
        if len(self.evidence) < 50:  # evidence cap, guard against blow-up
            self.evidence.append(
                {
                    "event_idx": ev.idx,
                    "ts": ev.ts.isoformat() if ev.ts else None,
                    "summary": summary[:200],
                }
            )


@dataclass
class TimelineEntry:
    idx: int
    ts: datetime | None
    kind: str  # session_start | session_end | compaction | transition
    component_id: str | None = None
    component_type: str | None = None
    state: str | None = None  # loaded | invoked
    detail: str = ""
    confidence: str = "exact"
    est_tokens: int = 0  # estimated context tokens this event contributed (0 = unknown)
    is_sidechain: bool = False  # happened in a subagent's own window, not the session's


@dataclass
class FileRead:
    """agent file-read event (not limited to manifest components; primary data for context injection order)."""

    idx: int
    ts: datetime | None
    path: str  # relative path if inside repo, absolute path if outside
    tool: str  # Read | Bash
    confidence: str  # exact | heuristic
    component_id: str | None = None  # if it also matches a manifest component
    is_sidechain: bool = False  # read landed in a subagent's window, not the session's
    line_range: tuple[int, int | None] | None = None  # Read offset/limit; None = unspecified


@dataclass
class Attribution:
    usages: dict[tuple[str, str], UsageAgg] = field(default_factory=dict)
    timeline: list[TimelineEntry] = field(default_factory=list)
    file_reads: list[FileRead] = field(default_factory=list)
    git_commit_count: int = 0
    git_commit_evidence: list[dict] = field(default_factory=list)
    compaction_count: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    turns: int = 0
    prompt_digest: str | None = None
    summary_text: str | None = None
    total_tokens: int = 0  # Σ usage(input+cache_read+cache_creation+output)

    def record_file_read(self, ev: Event, path: str, tool: str, confidence: str,
                         component_id: str | None, est_tokens: int = 0,
                         line_range: tuple[int, int | None] | None = None) -> None:
        """Record a file-read event (context injection order); non-manifest files also enter the timeline."""
        self.file_reads.append(
            FileRead(idx=ev.idx, ts=ev.ts, path=clean(path), tool=tool,
                     confidence=confidence, component_id=component_id,
                     is_sidechain=ev.is_sidechain, line_range=line_range)
        )
        if component_id is None:  # component files already produce a transition via mark(), avoid duplicate rows
            self.timeline.append(
                TimelineEntry(idx=ev.idx, ts=ev.ts, kind="file_read",
                              detail=_tag(ev, f"{tool} {path}"), confidence=confidence,
                              est_tokens=est_tokens, is_sidechain=ev.is_sidechain)
            )

    def mark(self, comp: Component, state: str, ev: Event, summary: str,
             confidence: str, est_tokens: int = 0) -> None:
        if state not in comp.states:
            return
        summary = _tag(ev, summary)
        agg = self.usages.setdefault((comp.id, state), UsageAgg())
        agg.add(ev, summary, confidence)
        self.timeline.append(
            TimelineEntry(
                idx=ev.idx, ts=ev.ts, kind="transition",
                component_id=comp.id, component_type=comp.type,
                state=state, detail=summary, confidence=confidence,
                est_tokens=est_tokens, is_sidechain=ev.is_sidechain,
            )
        )


def _match_path(comp_path: str | None, target: str, repo_root: Path) -> bool:
    if not comp_path:
        return False
    try:
        cand = Path(target)
        if not cand.is_absolute():
            cand = repo_root / cand
        return cand.resolve() == (repo_root / comp_path).resolve()
    except (OSError, ValueError):
        return False


def _rel_or_abs(target: str, repo_root: Path) -> str:
    try:
        p = Path(target)
        if not p.is_absolute():
            p = repo_root / p
        return str(p.resolve().relative_to(repo_root.resolve()))
    except (OSError, ValueError):
        return target


def _read_range(tin: dict) -> tuple[int, int | None] | None:
    """Line range from Read's offset/limit input (1-based, inclusive); None when neither
    is given — the tool then caps at 2000 lines, so claiming "all" would overstate."""
    off, lim = tin.get("offset"), tin.get("limit")
    if not isinstance(off, int) and not isinstance(lim, int):
        return None
    start = off if isinstance(off, int) and off > 0 else 1
    return (start, start + lim - 1 if isinstance(lim, int) else None)


def _under_dir(comp_dir: Path, target: str, repo_root: Path) -> bool:
    try:
        cand = Path(target)
        if not cand.is_absolute():
            cand = repo_root / cand
        cand.resolve().relative_to(comp_dir.resolve())
        return True
    except (OSError, ValueError):
        return False


def attribute(parsed: ParsedSession, components: list[Component],
              repo_root: Path) -> Attribution:
    att = Attribution()
    comps = [c for c in components if not c.missing]

    def _index(type_: str) -> dict[str, list[Component]]:
        """name → components. A transcript names a component, never a provenance, so one name
        can legitimately resolve to several manifest entries (a local skill and a plugin skill
        both called `help`); scanner._dedupe keeps both, so the index must too."""
        idx: dict[str, list[Component]] = {}
        for c in comps:
            if c.type == type_:
                idx.setdefault(c.name, []).append(c)
        return idx

    skills = _index("skill")
    commands = _index("command")
    agents = _index("subagent")
    mcps = _index("mcp")
    all_skills = [c for c in comps if c.type == "skill"]
    hooks = [c for c in comps if c.type == "hook"]
    claude_mds = [c for c in comps if c.type == "claude_md"]
    files = [c for c in comps if c.type == "file"]

    # tool_result index (I verdict requires "result is not error")
    results: dict[str, Event] = {}
    for ev in parsed.events:
        if ev.kind == "tool_result" and ev.tool_use_id:
            results[ev.tool_use_id] = ev

    # registered: every non-missing manifest component is R (A7.8; limitation documented in README)
    if parsed.events:
        first = parsed.events[0]
        for c in comps:
            agg = att.usages.setdefault((c.id, "registered"), UsageAgg())
            agg.add(first, "registered in manifest", "exact")

    # session statistics
    ts_list = [e.ts for e in parsed.events if e.ts is not None]
    att.started_at = min(ts_list) if ts_list else None
    att.ended_at = max(ts_list) if ts_list else None
    att.timeline.append(
        TimelineEntry(idx=-1, ts=att.started_at, kind="session_start",
                      detail=f"cc {parsed.cc_version or '?'}")
    )

    # root/global CLAUDE.md: always L, timeline anchored at session start (A7.7).
    # Though system-injected, it is still content that entered context → listed at the top of file loads.
    for c in claude_mds:
        if c.name in ("root", "global") and parsed.events:
            att.mark(c, "loaded", parsed.events[0],
                     f"{'root' if c.name == 'root' else 'global'} CLAUDE.md (always injected)", "exact",
                     est_tokens=c.tokens_est or 0)
            att.record_file_read(parsed.events[0], c.path or c.source or c.name,
                                 "system-injected", "exact", c.id)

    def _confidence(cands: list[Component], base: str) -> str:
        """Ambiguity is itself a downgrade: when several same-named components match, the
        signal cannot say which one ran, so every candidate is credited heuristically."""
        return base if len(cands) == 1 else "heuristic"

    def _skills_by_name(raw: str | None) -> list[Component]:
        if not raw:
            return []
        if raw in skills:
            return skills[raw]
        if ":" in raw:  # plugin:skill form — the plugin half narrows the provenance
            plugin, short = raw.split(":", 1)
            cands = skills.get(short, [])
            return [c for c in cands if c.source == f"plugin:{plugin}"] or cands
        return []

    def _skill_dir_matches(c: Component, path_str: str) -> bool:
        for ref in (c.path, c.source):
            # a plugin skill's source is the label `plugin:<name>`, not a directory
            if ref and not ref.startswith("plugin:") and path_str.endswith(str(Path(ref).parent)):
                return True
        return False

    def _skills_for_expansion(path_str: str) -> list[Component]:
        """The expansion message carries the skill's base directory, which disambiguates
        same-named skills from different provenances when the name alone cannot."""
        if not path_str:
            return []
        cands = skills.get(Path(path_str).name, [])
        if len(cands) > 1:
            narrowed = [c for c in cands if _skill_dir_matches(c, path_str)]
            if narrowed:
                return narrowed
        if not cands:
            cands = [c for c in all_skills if _skill_dir_matches(c, path_str)]
        return cands

    for ev in parsed.events:
        # line-level attributionSkill (spike #3 auxiliary L signal). Checked ahead of the kind
        # dispatch because the assistant_msg / user_msg branches below `continue`: a skill turn
        # that produces only text and calls no tools would otherwise never register. Skipped on
        # the expansion message itself, which the user_msg branch marks with better evidence.
        if ev.attribution_skill and not (
            ev.kind == "user_msg" and (ev.text or "").startswith(SKILL_BASE_DIR_PREFIX)
        ):
            cands = _skills_by_name(ev.attribution_skill)
            conf = _confidence(cands, "exact")
            for comp in cands:  # not double-counted per skill per line
                if (comp.id, "loaded") not in att.usages:
                    att.mark(comp, "loaded", ev, f"attributionSkill={ev.attribution_skill}", conf,
                             est_tokens=comp.tokens_body_est or comp.tokens_est or 0)

        if ev.kind == "compaction":
            att.compaction_count += 1
            att.timeline.append(
                TimelineEntry(idx=ev.idx, ts=ev.ts, kind="compaction",
                              detail=_tag(ev, "compaction"), is_sidechain=ev.is_sidechain)
            )
            continue

        if ev.kind == "assistant_msg" and ev.usage:
            att.total_tokens += (
                ev.usage.input_tokens + ev.usage.output_tokens
                + ev.usage.cache_creation_input_tokens + ev.usage.cache_read_input_tokens
            )
            continue

        if ev.kind == "summary" and ev.text and not att.summary_text:
            att.summary_text = clean(ev.text)
            continue

        if ev.kind == "user_msg":
            text = ev.text or ""
            if not ev.is_sidechain:
                # session stats and command detection are main-chain concepts: a sidechain
                # "user" message is the dispatch prompt the main agent wrote, not a user turn
                stripped = text.strip()
                if stripped:
                    # the caveat preamble is injected boilerplate, not a user turn
                    if not stripped.startswith("Caveat:"):
                        att.turns += 1
                    # digest prefers the real prompt over caveat / command-marker boilerplate
                    if att.prompt_digest is None or (
                        _is_boilerplate(att.prompt_digest) and not _is_boilerplate(stripped)
                    ):
                        att.prompt_digest = clean(stripped)[:80]
                # command marker (A7.2, spike #4 main case exact)
                m = COMMAND_MARKER_RE.search(text)
                cmd_name = None
                if m:
                    cmd_name = m.group(1)
                elif text.startswith("/"):
                    cmd_name = text.split()[0].lstrip("/") if text.split() else None
                if cmd_name:
                    # namespaced local commands index under their full name (frontend:review);
                    # a qualified miss falls back to the short name, narrowed by the plugin half
                    # when it matches a provenance (mirrors _skills_by_name)
                    cands = commands.get(cmd_name)
                    if not cands and ":" in cmd_name:
                        plugin, short = cmd_name.split(":", 1)
                        cands = commands.get(short) or []
                        cands = [c for c in cands if c.source == f"plugin:{plugin}"] or cands
                    cands = cands or []
                    conf = _confidence(cands, "exact")
                    for comp in cands:
                        att.mark(comp, "invoked", ev, f"/{cmd_name}", conf,
                                 est_tokens=comp.tokens_est or 0)
            # skill expansion (A7.1 L, spike #3 main case exact) — sidechains included:
            # a skill a subagent loads is still this session's scaffold usage
            if text.startswith(SKILL_BASE_DIR_PREFIX):
                # partition (not splitlines()[0]) — an empty remainder must not crash the scan
                path_str = text[len(SKILL_BASE_DIR_PREFIX):].partition("\n")[0].strip()
                cands = _skills_for_expansion(path_str)
                conf = _confidence(cands, "exact")
                for comp in cands:
                    att.mark(comp, "loaded", ev, f"skill expanded: {path_str}", conf,
                             est_tokens=comp.tokens_body_est or comp.tokens_est or 0)
            continue

        if ev.kind == "hook":
            # A7.5: hook I best-effort; confidence always heuristic
            candidates = [
                h for h in hooks
                if h.hook_event and ev.hook_event
                and h.hook_event.lower() == ev.hook_event.lower()
            ]
            if len(candidates) > 1 and ev.tool_input and ev.tool_input.get("command"):
                cmd = ev.tool_input["command"]
                narrowed = [h for h in candidates if any(c in cmd or cmd in c
                                                         for c in h.hook_commands)]
                if narrowed:
                    candidates = narrowed
            for h in candidates:
                att.mark(h, "invoked", ev,
                         f"hook {ev.hook_name or ev.hook_event} triggered", "heuristic")
            continue

        if ev.kind != "tool_use":
            continue

        tool = ev.tool_name or ""
        tin = ev.tool_input or {}
        result = results.get(ev.tool_use_id or "")
        result_ok = result is not None and not result.is_error
        result_est = estimate_tokens(result.text or "") if result is not None else 0
        input_est = sum(estimate_tokens(v) for v in tin.values() if isinstance(v, str))

        # agent action events (first-class timeline display): file edits and command execution.
        # Recorded as soon as the action happens (unlike loading); failures are marked rather than dropped.
        bash_action: TimelineEntry | None = None
        if tool in FILE_EDIT_TOOLS or tool == "Bash":
            if tool == "Bash":
                detail = (tin.get("command") or "").strip().replace("\n", " ⏎ ")
            else:
                target = tin.get("file_path") or tin.get("notebook_path") or ""
                detail = _rel_or_abs(target, repo_root) if target else ""
            failed = result is not None and result.is_error
            # Bash output may turn out to be file reads (attributed below), so it starts
            # with the command only; other tools count input + result now.
            entry = TimelineEntry(idx=ev.idx, ts=ev.ts, kind="action",
                                  component_type=tool,
                                  detail=_tag(ev, detail + (" (failed)" if failed else "")),
                                  est_tokens=input_est if tool == "Bash"
                                  else input_est + result_est,
                                  is_sidechain=ev.is_sidechain)
            att.timeline.append(entry)
            if tool == "Bash":
                bash_action = entry

        if tool == "Skill":
            cands = _skills_by_name(tin.get("skill"))
            if result_ok:  # I = execution completed (result is not error)
                conf = _confidence(cands, "exact")
                for comp in cands:
                    att.mark(comp, "invoked", ev, f"Skill({tin.get('skill')}) completed", conf,
                             est_tokens=result_est)
            continue

        if tool in ("Task", "Agent"):  # Agent since ~2.1.2xx, Task before (SPIKES.md W2 #13)
            sub = tin.get("subagent_type")
            cands = agents.get(sub, []) if isinstance(sub, str) else []
            if not cands and isinstance(sub, str) and ":" in sub:
                # plugin agents dispatch qualified (plugin:name) but index under the short
                # name; the plugin half narrows the provenance (mirrors _skills_by_name)
                plugin, short = sub.split(":", 1)
                cands = agents.get(short, [])
                cands = [c for c in cands if c.source == f"plugin:{plugin}"] or cands
            conf = _confidence(cands, "exact")
            for comp in cands:
                att.mark(comp, "invoked", ev, f"{tool}(subagent_type={sub})", conf,
                         est_tokens=result_est)
            continue

        mm = MCP_TOOL_RE.match(tool)
        if mm:
            cands = mcps.get(mm.group(1), [])
            conf = _confidence(cands, "exact")
            for comp in cands:
                att.mark(comp, "invoked", ev, f"{tool}", conf, est_tokens=result_est)
            continue

        if tool == "Read":
            target = tin.get("file_path")
            if isinstance(target, str) and result_ok:
                matched_cid: str | None = None
                for c in [*files, *claude_mds]:
                    if _match_path(c.path, target, repo_root):
                        att.mark(c, "loaded", ev, f"Read {target}", "exact",
                                 est_tokens=result_est)
                        matched_cid = c.id
                # degraded fallback: Read SKILL.md → skill L (heuristic)
                if target.endswith("SKILL.md"):
                    for comp in skills.get(Path(target).parent.name, []):
                        att.mark(comp, "loaded", ev, f"Read {target}", "heuristic",
                                 est_tokens=result_est)
                        matched_cid = matched_cid or comp.id
                att.record_file_read(ev, _rel_or_abs(target, repo_root), "Read",
                                     "exact", matched_cid, est_tokens=result_est,
                                     line_range=_read_range(tin))
            continue

        if tool in FILE_EDIT_TOOLS:
            target = tin.get("file_path") or tin.get("notebook_path")
            if isinstance(target, str):
                # subdirectory CLAUDE.md directory activity heuristic (spike #6 degraded)
                for c in claude_mds:
                    if c.name in ("root", "global") or not c.path:
                        continue
                    if _under_dir(repo_root / Path(c.path).parent, target, repo_root):
                        att.mark(c, "loaded", ev, f"directory activity: {tool} {target}", "heuristic")
            continue

        if tool == "Bash":
            cmd = tin.get("command")
            if not isinstance(cmd, str):
                continue
            result_text = result.text if result is not None else None
            # git commit detection (feeds the hook MISS miss_when verdict)
            if bash_heuristics.detect_git_commit(cmd, result_text):
                att.git_commit_count += 1
                att.git_commit_evidence.append(
                    {"event_idx": ev.idx, "ts": ev.ts.isoformat() if ev.ts else None,
                     "summary": clean(cmd)[:200]}
                )
            # file-read heuristic → file/claude_md L; not counted when tool_result is error (reduces false positives)
            if result_ok:
                valid_paths = []
                for p in bash_heuristics.extract_read_paths(cmd, ev.cwd):
                    # last gate for the heuristic: the file must actually exist (zero tolerance for
                    # false positives; the cost is that files deleted after the session are missed — documented in README)
                    try:
                        if Path(p).is_file():
                            valid_paths.append(p)
                    except OSError:
                        continue
                # result tokens: split across detected file reads; a plain command output
                # (no reads) stays attributed to the action itself
                if valid_paths:
                    per_path_est = result_est // len(valid_paths)
                elif bash_action is not None:
                    bash_action.est_tokens += result_est
                for p in valid_paths:
                    matched_cid = None
                    for c in [*files, *claude_mds]:
                        if _match_path(c.path, p, repo_root):
                            att.mark(c, "loaded", ev, f"Bash file read: {p}", "heuristic",
                                     est_tokens=per_path_est)
                            matched_cid = c.id
                    att.record_file_read(ev, _rel_or_abs(p, repo_root), "Bash",
                                         "heuristic", matched_cid, est_tokens=per_path_est)
            elif bash_action is not None:
                bash_action.est_tokens += result_est  # error output still lands in context
            # subdirectory CLAUDE.md directory activity (Bash cwd)
            for c in claude_mds:
                if c.name in ("root", "global") or not c.path:
                    continue
                if ev.cwd and _under_dir(repo_root / Path(c.path).parent, ev.cwd, repo_root):
                    att.mark(c, "loaded", ev, f"directory activity: Bash cwd={ev.cwd}", "heuristic")
            continue

    att.timeline.append(
        TimelineEntry(idx=10**9, ts=att.ended_at, kind="session_end",
                      detail=f"Σ {att.total_tokens:,} tok")
    )
    # ts missing/out-of-order → sort by idx (spike #1 degraded, marked "order inferred" on display)
    att.timeline.sort(key=lambda t: t.idx)
    return att
