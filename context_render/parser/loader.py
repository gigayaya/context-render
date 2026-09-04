"""JSONL transcript → Event stream.

Isolation principle: all transcript-format parsing is encapsulated in the parser layer; on an
unknown event, degrade to a warning and continue — MUST NOT crash. Claude Code version
bumps only touch this layer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..textutil import clean

EventKind = Literal[
    "user_msg",
    "assistant_msg",
    "tool_use",
    "tool_result",
    "hook",
    "summary",
    "compaction",  # v3: known-form recognition, samples locked with self-made fixtures
    "system",
    "unknown",
]

# known auxiliary types (do not trigger degraded), mapped to kind=system
KNOWN_AUX_TYPES = {
    "attachment",
    "last-prompt",
    "mode",
    "permission-mode",
    "file-history-snapshot",
    "ai-title",
    "agent-name",
    "progress",
    "queue-operation",
    # observed 2026-09-05 in cc 2.1.251–2.1.260
    "atis-latch",
    "bridge-session",
    "cost-state",
    "file-history-delta",
}

SKILL_BASE_DIR_PREFIX = "Base directory for this skill:"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class Event:
    idx: int  # line order within the session stream (0-based, renumbered when sidechain files merge in), referenced by evidence
    kind: EventKind
    ts: datetime | None = None  # keep original precision, no truncation (timeline depends on it)
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_use_id: str | None = None
    is_error: bool = False  # tool_result is an error
    text: str | None = None
    usage: Usage | None = None
    model: str | None = None
    cwd: str | None = None
    raw_type: str = ""
    # hook event
    hook_name: str | None = None
    hook_event: str | None = None
    # line-level skill attribution field (auxiliary signal)
    attribution_skill: str | None = None
    is_sidechain: bool = False
    # subagent identity of a sidechain event (meta.json agentType, else the file's agent id)
    agent: str | None = None


@dataclass
class ParsedSession:
    session_id: str
    path: str
    events: list[Event]
    cc_version: str | None = None
    cwd: str | None = None
    unknown_type_counts: dict[str, int] = field(default_factory=dict)
    bad_line_count: int = 0
    ts_out_of_order: bool = False

    @property
    def parse_status(self) -> str:
        return "degraded" if (self.unknown_type_counts or self.bad_line_count) else "ok"


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)  # py3.11+: trailing Z parsed natively
    except ValueError:
        return None
    # normalize to UTC: naive timestamps are treated as UTC, aware ones converted, so
    # mixed transcripts stay comparable (subtraction/sorting) and the isoformat strings
    # the DB stores and compares lexicographically all share one offset
    return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _parse_usage(msg: dict) -> tuple[Usage | None, str | None]:
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None, msg.get("model")
    usage = Usage(
        input_tokens=int(u.get("input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cache_creation_input_tokens=int(u.get("cache_creation_input_tokens") or 0),
        cache_read_input_tokens=int(u.get("cache_read_input_tokens") or 0),
    )
    return usage, msg.get("model")


def _text_of_content(content) -> str:
    """Extract plain text from user/assistant content (str or block list)."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        parts.extend(block.get("text") or "" for block in content
                     if isinstance(block, dict) and block.get("type") == "text")
    return "\n".join(parts)


def _events_from_line(idx: int, obj: dict) -> list[Event]:
    base = {
        "idx": idx,
        "ts": _parse_ts(obj.get("timestamp")),
        "cwd": obj.get("cwd"),
        "raw_type": str(obj.get("type", "")),
        "attribution_skill": obj.get("attributionSkill"),
        "is_sidechain": bool(obj.get("isSidechain")),
    }
    t = obj.get("type")
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    out: list[Event] = []

    if t == "user":
        # compaction summary (known form)
        if obj.get("isCompactSummary"):
            return [Event(**base, kind="compaction", text=_text_of_content(content))]
        emitted_text = False
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    out.append(
                        Event(
                            **base,
                            kind="tool_result",
                            tool_use_id=block.get("tool_use_id"),
                            is_error=bool(block.get("is_error")),
                            text=_text_of_content(block.get("content"))
                            if not isinstance(block.get("content"), str)
                            else block["content"],
                        )
                    )
                elif block.get("type") == "text":
                    out.append(Event(**base, kind="user_msg", text=block.get("text") or ""))
                    emitted_text = True
        elif isinstance(content, str):
            out.append(Event(**base, kind="user_msg", text=content))
            emitted_text = True
        if not out and not emitted_text:
            out.append(Event(**base, kind="user_msg", text=_text_of_content(content)))
        return out

    if t == "assistant":
        usage, model = _parse_usage(msg)
        out.append(
            Event(**base, kind="assistant_msg", usage=usage, model=model,
                  text=_text_of_content(content))
        )
        if isinstance(content, list):
            out.extend(
                Event(
                    **base,
                    kind="tool_use",
                    tool_name=block.get("name"),
                    tool_input=block.get("input")
                    if isinstance(block.get("input"), dict)
                    else None,
                    tool_use_id=block.get("id"),
                )
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )
        return out

    if t == "system":
        subtype = obj.get("subtype")
        if subtype == "compact_boundary":  # known form
            return [Event(**base, kind="compaction")]
        if subtype == "stop_hook_summary":
            infos = obj.get("hookInfos") if isinstance(obj.get("hookInfos"), list) else []
            for info in infos or [{}]:
                cmd = info.get("command") if isinstance(info, dict) else None
                out.append(
                    Event(**base, kind="hook", hook_event="Stop",
                          tool_input={"command": cmd} if cmd else None, text=cmd)
                )
            return out
        return [Event(**base, kind="system", text=obj.get("content") or obj.get("text"))]

    if t == "summary":
        return [Event(**base, kind="summary", text=obj.get("summary"))]

    if t == "attachment":
        att = obj.get("attachment") if isinstance(obj.get("attachment"), dict) else {}
        att_type = att.get("type") or ""
        if att_type.startswith("hook_"):
            hook_name = att.get("hookName")
            return [
                Event(
                    **base,
                    kind="hook",
                    hook_name=hook_name,
                    hook_event=att.get("hookEvent")
                    or (hook_name.split(":", 1)[0] if isinstance(hook_name, str) else None),
                    tool_input={"attachment_type": att_type},
                )
            ]
        return [Event(**base, kind="system", text=att_type)]

    if t in KNOWN_AUX_TYPES:
        return [Event(**base, kind="system")]

    return [Event(**base, kind="unknown")]


def index_tool_results(events: list[Event]) -> dict[str, Event]:
    """tool_use_id → the tool_result that answers it. The first result wins: a later line
    carrying the same id is a re-emission, not the outcome the agent acted on. One
    reading shared by attribution (rules), self-derivation facts and the stale gauge."""
    results: dict[str, Event] = {}
    for ev in events:
        if ev.kind == "tool_result" and ev.tool_use_id and ev.tool_use_id not in results:
            results[ev.tool_use_id] = ev
    return results


def _read_objs(path: Path) -> tuple[list[tuple[int, dict]], int]:
    """(file line number, parsed object) pairs; bad lines are counted, never raised."""
    objs: list[tuple[int, dict]] = []
    bad = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for idx, raw in enumerate(fh):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(obj, dict):
                bad += 1
                continue
            objs.append((idx, obj))
    return objs, bad


def _agent_label(path: Path) -> str | None:
    """Subagent identity from the sibling .meta.json (agentType) — the readable name."""
    meta = path.with_name(path.stem + ".meta.json")
    try:
        obj = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = obj.get("agentType") if isinstance(obj, dict) else None
    return v if isinstance(v, str) else None


_MIN_TS = datetime.min.replace(tzinfo=UTC)


def parse_file(path: Path, sidechains: Sequence[Path] = ()) -> ParsedSession:
    """Parse line by line; fields are "use if present, degrade if missing", no strict schema validation.

    sidechains: this session's subagent transcripts (separate files under
    <session-id>/subagents/). Their lines merge into one chronological stream — sorted by
    timestamp (fill-forward for ts-less lines, so intra-file order survives) and idx
    renumbered over the merged stream. Without sidechains idx stays the raw file line
    number, keeping single-file evidence refs stable.
    """
    main_objs, bad_lines = _read_objs(path)

    cc_version: str | None = None
    cwd: str | None = None
    for _, obj in main_objs:  # session identity comes from the main chain only
        if cc_version is None and isinstance(obj.get("version"), str):
            # version strings flow into rendered warnings and timeline headers → sanitize
            cc_version = clean(obj["version"])
        if cwd is None and isinstance(obj.get("cwd"), str):
            cwd = obj["cwd"]
        if cc_version and cwd:
            break

    stream: list[tuple[int, str | None, dict]]
    if sidechains:
        keyed: list[tuple[datetime, str | None, dict]] = []
        streams: list[tuple[str | None, list[tuple[int, dict]]]] = [(None, main_objs)]
        for sp in sidechains:
            objs, bad = _read_objs(sp)
            bad_lines += bad
            label = _agent_label(sp) or sp.stem.removeprefix("agent-")
            streams.append((label, objs))
        for label, objs in streams:
            last = _MIN_TS
            for _, obj in objs:
                ts = _parse_ts(obj.get("timestamp"))
                if ts is not None:
                    last = ts
                keyed.append((last, label, obj))
        # stable sort: intra-file order survives (fill-forward keys are monotone per file),
        # equal timestamps keep the main chain first
        keyed.sort(key=lambda k: k[0])
        stream = [(idx, label, obj) for idx, (_, label, obj) in enumerate(keyed)]
    else:
        stream = [(idx, None, obj) for idx, obj in main_objs]

    events: list[Event] = []
    unknown_counts: dict[str, int] = {}
    for idx, label, obj in stream:
        try:
            new_events = _events_from_line(idx, obj)
        except Exception:  # noqa: BLE001 - degrade, never crash
            # any single-line parse error degrades, does not crash
            bad_lines += 1
            continue
        for ev in new_events:
            if label is not None:
                ev.is_sidechain = True
                ev.agent = label
            if ev.kind == "unknown":
                unknown_counts[ev.raw_type] = unknown_counts.get(ev.raw_type, 0) + 1
        events.extend(new_events)

    # ts out-of-order detection (degrade: timeline sorted by idx and marked "order inferred").
    # Parallel tool_result/sidechain interleaving within tens of seconds is common and normal; only a large regression degrades and is marked.
    prev = None
    out_of_order = False
    for ev in events:
        if ev.ts is None:
            continue
        if prev is not None and (prev - ev.ts).total_seconds() > 300:
            out_of_order = True
            break
        prev = ev.ts if prev is None else max(prev, ev.ts)

    return ParsedSession(
        session_id=path.stem,
        path=str(path),
        events=events,
        cc_version=cc_version,
        cwd=cwd,
        unknown_type_counts=unknown_counts,
        bad_line_count=bad_lines,
        ts_out_of_order=out_of_order,
    )
