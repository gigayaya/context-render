"""Context-window map: one rectangular bar = the session's context window over time.

    context window map (▼ injected · ▲ actions · ⟐ compaction · height ≈ tokens · # = timeline row)
               2    5 7    11  14
    loads      ▼    ▼ ▼▼   ▼   ▼
              ┌──────────────────────────┐
              │▃    █ ▆▂▅   ▂  ▆▂     ▂  │ ▼ injected
              │    ▁      ▂  ▁ ⟐▅  ▂▁ ▁▂│ ▲ actions
              └──────────────────────────┘
    actions      ▲ ▲▲  ▲▲▲ ▲
                 3 6   9   13
              14:02:31            14:31:05

Top ▼ marks are context injections (file loads / component L-transitions), bottom ▲
marks are agent actions (edit/write/bash and I-invocations); each mark is labeled with
its row number in the timeline listing, so the two views cross-reference (dense spots
stack labels into extra lanes; unplaceable labels are dropped, neighbors still anchor).
Inside the box each event paints a block in its own lane and color — cyan loads on the
top row, yellow actions on the bottom row — whose height tracks the event's estimated
context tokens (√-compressed against the session's largest event, so one huge file read
doesn't flatten everything else; events of unknown size show the minimum tick).
⟐ marks compaction across both rows.

Below the event bar, a second `window` bar shows occupancy (green): prompt-side token
usage per turn against the model window (config context_window_tokens, default 200k;
a peak above it snaps the denominator up to the next known tier — see WINDOW_TIERS),
linear scale, carried forward between samples; compaction drops show up as a dip. Both
bars share one column mapping, so occupancy changes line up vertically with the events
that caused them. x-position is by event *rank* among displayed events — raw file line
numbers would let timeline-invisible lines (e.g. sidechains) squeeze all activity into
a corner.

Pure stdlib drawing; fixed width so terminal and md forms emit identical lines (AC3/AC9).
"""

from __future__ import annotations

import math

from .ansi import Style
from .charts import _short
from .timeline import _fmt_ts

WIDTH = 60  # bar interior columns; fixed for cross-form line identity
# Known Claude context-window sizes, ascending. Transcripts carry no window-size field,
# so a peak above the configured window is the only hard evidence of a bigger window;
# the denominator then snaps to the smallest tier that fits (peak itself as last resort).
WINDOW_TIERS = (200_000, 1_000_000)
GUTTER = 10  # row-label gutter before the bar's left border
FILL = " ▁▂▃▄▅▆▇█"  # 9 levels; 0 unused in event lanes (any event gets at least ▁)
MAX_LANES = 3  # label lanes per side


def _arrow_row(cols: set[int], symbol: str) -> str:
    return "".join(symbol if i in cols else " " for i in range(WIDTH)).rstrip()


def _label_lanes(events: list[tuple[int, int]]) -> list[str]:
    """Greedy lane allocation of `str(no)` labels anchored at their arrow column.

    events = [(col, timeline_no)] in timeline order; a label needs one space of
    gap to the previous label in its lane so adjacent numbers never fuse.
    """
    lanes: list[list[str]] = []
    ends: list[int] = []  # first free column per lane
    for b, no in sorted(events):
        lbl = str(no)
        for li in range(len(lanes)):
            if b >= ends[li]:
                break
        else:
            if len(lanes) >= MAX_LANES:
                continue  # dropped; neighbors still anchor the area
            lanes.append([" "] * WIDTH)
            ends.append(0)
            li = len(lanes) - 1
        for j, ch in enumerate(lbl):
            if b + j < WIDTH:
                lanes[li][b + j] = ch
        ends[li] = b + len(lbl) + 1
    return ["".join(lane).rstrip() for lane in lanes]


def _blocks_row(cells: dict[int, int], compactions: set[int], max_est: int,
                paint, style: Style) -> str:
    """One event lane: block height = √(est/max) over 8 levels; ⟐ cuts through."""
    parts = []
    for i in range(WIDTH):
        if i in compactions:
            parts.append(style.magenta("⟐"))
        elif i in cells:
            e = cells[i]
            lvl = 1 if max_est <= 0 or e <= 0 else max(
                1, round(math.sqrt(e / max_est) * (len(FILL) - 1)))
            parts.append(paint(FILL[lvl]))
        else:
            parts.append(" ")
    return "".join(parts)


def context_map_lines(timeline: list[dict], samples: list[dict],
                      style: Style | None = None,
                      window_tokens: int = 200_000) -> list[str]:
    """Render the map; empty timeline → no lines. samples may be empty (no usage data)."""
    style = style or Style()
    # the map draws the session's own window; sidechain rows belong to a subagent's window
    # (they stay in the timeline listing, tagged) — mirrors the context_samples exclusion
    timeline = [e for e in timeline if not e.get("sidechain")]
    # one shared column mapping so the occupancy bar lines up with the event bar
    refs = sorted({e["evidence_ref"] for e in timeline if e.get("evidence_ref") is not None}
                  | {s["idx"] for s in samples})
    if not refs:
        return []
    # event rank → bar column (uniform spacing of displayed events)
    last_rank = max(1, len(refs) - 1)
    col = {idx: min(WIDTH - 1, rank * (WIDTH - 1) // last_rank)
           for rank, idx in enumerate(refs)}

    loads: list[tuple[int, int]] = []  # (col, timeline row number)
    acts: list[tuple[int, int]] = []
    load_cells: dict[int, int] = {}  # col → Σ est tokens landing there
    act_cells: dict[int, int] = {}
    compactions: set[int] = set()
    for no, e in enumerate(timeline, 1):
        ref = e.get("evidence_ref")
        if ref is None:
            continue
        b = col[ref]
        kind = e.get("kind")
        est = e.get("est_tokens") or 0
        if kind == "compaction":
            compactions.add(b)
        elif kind == "file_read" or e.get("transition") == "loaded":
            loads.append((b, no))
            load_cells[b] = load_cells.get(b, 0) + est
        elif kind == "action" or e.get("transition") == "invoked":
            acts.append((b, no))
            act_cells[b] = act_cells.get(b, 0) + est

    # shared height scale so the two lanes stay comparable
    max_est = max([*load_cells.values(), *act_cells.values()], default=0)

    # legend symbols carry the same colors they have in the chart
    head = ("  " + style.bold("context window map")
            + style.dim(" (") + style.cyan("▼") + style.dim(" injected · ")
            + style.yellow("▲") + style.dim(" actions · ")
            + style.magenta("⟐") + style.dim(" compaction · height ≈ tokens"
                                             " · # = timeline row)"))
    gut = " " * GUTTER
    border = style.dim("│")
    lines = [head]
    for lane in reversed(_label_lanes(loads)):
        lines.append((gut + " " + style.cyan(lane)).rstrip())
    lines.append(f"{'  loads':<{GUTTER}} {style.cyan(_arrow_row({b for b, _ in loads}, '▼'))}".rstrip())
    lines.append(gut + style.dim("┌" + "─" * WIDTH + "┐"))
    lines.append(gut + border
                 + _blocks_row(load_cells, compactions, max_est, style.cyan, style)
                 + border + " " + style.cyan("▼") + style.dim(" injected"))
    lines.append(gut + border
                 + _blocks_row(act_cells, compactions, max_est, style.yellow, style)
                 + border + " " + style.yellow("▲") + style.dim(" actions"))
    lines.append(gut + style.dim("└" + "─" * WIDTH + "┘"))
    lines.append(f"{'  actions':<{GUTTER}} {style.yellow(_arrow_row({b for b, _ in acts}, '▲'))}".rstrip())
    for lane in _label_lanes(acts):
        lines.append((gut + " " + style.yellow(lane)).rstrip())

    # second bar: window occupancy (green, linear vs the model window, carried forward)
    if samples:
        peak = max(s["tokens"] for s in samples)
        scale = window_tokens
        if peak > scale:
            scale = next((t for t in WINDOW_TIERS if t >= peak), peak)
        occupancy: dict[int, int] = {}
        for s in samples:
            b = col[s["idx"]]
            occupancy[b] = max(occupancy.get(b, 0), s["tokens"])
        parts: list[str] = []
        level = 0
        for i in range(WIDTH):
            if i in occupancy:
                level = max(1, round(occupancy[i] / scale * (len(FILL) - 1)))
            if i in compactions:
                parts.append(style.magenta("⟐"))
            else:
                parts.append(style.green(FILL[level]))
        occ_note = style.dim(f" occupancy · peak {_short(peak)}/{_short(scale)} tok")
        lines.append(gut + style.dim("┌" + "─" * WIDTH + "┐"))
        lines.append(f"{'  window':<{GUTTER}}" + border + "".join(parts) + border + occ_note)
        lines.append(gut + style.dim("└" + "─" * WIDTH + "┘"))

    t0 = _fmt_ts(timeline[0].get("ts"))
    t1 = _fmt_ts(timeline[-1].get("ts"))
    if "--:--:--" not in (t0, t1):
        axis = f"{t0}{'':<{WIDTH - len(t0) - len(t1) + 2}}{t1}"
        lines.append(gut + style.dim(axis))
    return lines
