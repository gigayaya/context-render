"""Self-derivation aggregation: the `analyze` main table
and the session report's SELF-DERIVATION block share this grouping — the only differences
are scope (window vs one session) and which columns render.

A gauge, not a grader: rows state what information the agent went after and what that
cost; there are no scores, no verdicts, and no "you should".
"""

from __future__ import annotations

from collections import Counter

from ..attributor.facts import CODE_KEY, MAPPING_KEY, Fact
from ..config import Config
from ..store import Store

SCHEMA_VERSION = 2

_TOOL_LABEL = {"Grep": "Grep", "Glob": "Glob"}  # everything else renders lowercase as-is


def fact_dicts(facts: list[Fact], session_id: str) -> list[dict]:
    """Live-extraction facts in the same shape as `facts` table rows."""
    return [
        {
            "session_id": session_id,
            "idx": f.idx,
            "kind": f.kind,
            "key": f.key,
            "raw": f.raw,
            "tool": f.tool,
            "tokens_est": f.tokens_est,
            "occupancy_est": f.occupancy_est,
            "sidechain": int(f.sidechain),
            "confidence": f.confidence,
        }
        for f in facts
    ]


def _mapping_head(raw: str) -> str:
    if raw.startswith("find"):
        return "find -type d"
    if raw.startswith("tree"):
        return "tree"
    return "ls chains"


def aggregate_rows(fact_rows: list) -> list[dict]:
    """Group facts by canonical key → one table row per information need,
    sorted by direct tokens (descending)."""
    groups: dict[str, dict] = {}
    for f in fact_rows:
        g = groups.setdefault(
            f["key"],
            {
                "key": f["key"],
                "kind": "action" if f["key"] in (MAPPING_KEY, CODE_KEY) else "keyword",
                "sessions": set(),
                "times": 0,
                "tokens": 0,
                "occupancy": None,
                "raws": Counter(),
                "heads": [],
                "chain": False,
                "heuristic": False,
                "story_searches": Counter(),  # (tool, raw) -> n;repo-layout 的 raw 為 ""
                "story_reads": Counter(),     # basename -> n
            },
        )
        g["sessions"].add(f["session_id"])
        g["times"] += 1
        g["tokens"] += f["tokens_est"]
        if f["occupancy_est"] is not None:
            g["occupancy"] = (g["occupancy"] or 0) + f["occupancy_est"]
        if f["confidence"] == "heuristic":
            g["heuristic"] = True
        if f["kind"] == "chain_read":
            g["chain"] = True
            g["story_reads"][f["raw"].rsplit("/", 1)[-1]] += 1
        elif f["kind"] == "mapping":
            if f["key"] == CODE_KEY:
                g["raws"][f["raw"]] += 1  # method 欄要探測形態,不是 _mapping_head
                tool = _TOOL_LABEL.get(f["tool"], (f["tool"] or "bash").lower())
                g["story_searches"][(tool, f["raw"])] += 1
            else:
                head = _mapping_head(f["raw"])
                if head not in g["heads"]:
                    g["heads"].append(head)
                g["story_searches"][(head, "")] += 1
        else:
            g["raws"][f["raw"]] += 1
            head = _TOOL_LABEL.get(f["tool"], (f["tool"] or "bash").lower())
            if head not in g["heads"]:
                g["heads"].append(head)
            g["story_searches"][(head, f["raw"])] += 1

    rows = []
    for g in groups.values():
        if g["kind"] == "action" and g["key"] == CODE_KEY:
            # a code-structure row's method shows the probe shapes themselves —
            # "spellings" implies vocabulary guessing, which this is not
            top = [r for r, _ in g["raws"].most_common(3)]
            method = "probes: " + ", ".join(top)
            extra = len(g["raws"]) - len(top)
            if extra > 0:
                method += f" +{extra}"
            if g["chain"]:
                method += ", opened hits"
            if g["heuristic"]:
                method += " ~"
            label = g["key"]
        else:
            variants = len(g["raws"])
            method = ", ".join(g["heads"])
            if variants >= 2:  # the agent was guessing project vocabulary (§4.1 rule 5)
                method += f", tried {variants} spellings"
            if g["chain"]:
                method += ", opened hits" if method else "opened hits"
            if g["heuristic"]:
                method += " ~"
            label = g["key"] if g["kind"] == "action" else (
                g["raws"].most_common(1)[0][0] if g["raws"] else g["key"])
        rows.append(
            {
                "key": g["key"],
                "kind": g["kind"],
                "label": label,
                "method": method.strip(),
                "sessions": len(g["sessions"]),
                "times": g["times"],
                "tokens": g["tokens"],
                "occupancy": g["occupancy"],
                "heuristic": g["heuristic"],
                "story": {
                    "searches": [(t, raw, n) for (t, raw), n
                                 in g["story_searches"].most_common()],
                    "reads": list(g["story_reads"].most_common()),
                },
            }
        )
    rows.sort(key=lambda r: (-r["tokens"], -r["times"], r["key"]))
    return rows


def aggregate_analyze(store: Store, config: Config, since_iso: str | None,
                      since_label: str) -> tuple[dict, list[dict]]:
    """(analyze aggregate object, raw fact rows) — the fact rows feed --emit-prompt."""
    sessions = store.sessions_since(since_iso)
    session_ids = [r["id"] for r in sessions]
    covered, tool_output = store.facts_coverage(session_ids)
    fact_rows = [dict(r) for r in store.facts_for_sessions(session_ids)]
    rows = aggregate_rows(fact_rows)
    total_tokens = sum(r["tokens"] for r in rows)

    warnings = []
    if not sessions:
        warnings.append(
            "⚠ no ingested sessions in window — nothing to analyze; "
            "run `ctxr sync` or widen --since"
        )
    elif not covered:
        warnings.append(
            "⚠ no session in this window has extracted facts — run `ctxr sync` "
            "(sessions whose transcripts already expired can never be backfilled)"
        )

    agg = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "analyze",
        "window": {
            "label": since_label,
            "since": since_iso,
            "session_count": len(sessions),
            "facts_sessions": len(covered),
            "project": sessions[0]["project"] if sessions else "?",
        },
        "summary": {
            "tokens": total_tokens,
            "tool_output_tokens": tool_output,
            "pct": round(total_tokens / tool_output * 100, 1) if tool_output else None,
        },
        "rows": rows,
        "warnings": warnings,
    }
    return agg, fact_rows


def select_row(rows: list[dict], selector: str) -> dict | None:
    """--emit-prompt accepts the row number of the current sort (unstable across runs)
    or the canonical key string."""
    sel = selector.strip()
    if sel.isdigit():
        n = int(sel)
        if 1 <= n <= len(rows):
            return rows[n - 1]
        return None
    low = sel.lower()
    for r in rows:
        if r["key"] == low or r["label"].lower() == low:
            return r
    return None


def emit_prompt_text(agg: dict, fact_rows: list[dict], row: dict) -> str:
    """Evidence bundle for one row as a drafting prompt — plain text, zero API calls.
    It deliberately does not recommend what scaffold to write; the receiving agent judges."""
    facts = [f for f in fact_rows if f["key"] == row["key"]]
    w = agg["window"]
    lines = [
        "Scaffolding-gap evidence bundle (ctxr analyze --emit-prompt; "
        "generated offline, no scores, no verdicts)",
        "",
        f"what the agent was after: {row['label']}",
        f"canonical key: {row['key']}",
        f"window: {w['label']} — {row['sessions']} session(s), {row['times']} occurrence(s)",
        f"direct cost: ~{row['tokens']:,} tokens (tool-result size estimate, ceil(utf8/4))",
    ]
    if row["occupancy"] is not None:
        lines.append(
            f"window occupancy: ~{row['occupancy']:,} token-turns "
            "(tokens × assistant turns remaining until session end or next compaction; heuristic)"
        )
    lines += [
        "",
        "Every one of these actions is the agent asking a question the harness did not",
        "answer, then paying context window to answer it itself. Judge for yourself what",
        "change — if any — would provide this information up front; this tool does not",
        "recommend a specific scaffold form.",
        "",
    ]
    variants = Counter(f["raw"] for f in facts if f["kind"] == "search")
    if len(variants) >= 2:
        lines.append(f"variants tried ({len(variants)}): "
                     + ", ".join(sorted(variants)))
        lines.append("")
    lines.append("evidence (session  #event-idx  kind  tool  raw  ~tokens):")
    for f in facts:
        tag = "  [subagent]" if f["sidechain"] else ""
        conf = " ~" if f["confidence"] == "heuristic" else ""
        lines.append(
            f"  {f['session_id'][:8]}  #{f['idx']:<5} {f['kind']:<10} "
            f"{(f['tool'] or '?'):<9} {f['raw']}{tag}{conf}  ~{f['tokens_est']:,} tok"
        )
    lines += [
        "",
        "notes: kind=search is a keyword search; kind=mapping is repo-structure mapping;",
        "kind=chain_read is a file opened straight out of the previous search's results",
        "(merged into this row's cost). ~ marks heuristic attribution; all token numbers",
        "are estimates. Event idx refers to the merged transcript stream of that session.",
    ]
    return "\n".join(lines)
