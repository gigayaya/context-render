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

Two layers: `context_map_parts` builds rows of segments ({"t", "role", "no"}; role is a
semantic name — load/action/compaction/occupancy/dim/bold — and label segments carry their
timeline row number in `no`), `render_rows` paints them through Style. The ctxr-live mod
consumes the segment layer as JSON (`ctxr live`), so terminal, md and band share one
geometry. `width` defaults to WIDTH; reports never pass another value.

Pure stdlib drawing; fixed width so terminal and md forms emit identical lines.
"""

from __future__ import annotations

import math

from .ansi import Style
from .charts import FILL, _short  # FILL: 9 levels; 0 unused in event lanes (min ▁)
from .timeline import _fmt_ts

WIDTH = 60  # default bar interior columns; fixed for cross-form line identity
# Known Claude context-window sizes, ascending. Transcripts carry no window-size field,
# so a peak above the configured window is the only hard evidence of a bigger window;
# the denominator then snaps to the smallest tier that fits (peak itself as last resort).
WINDOW_TIERS = (200_000, 1_000_000)
GUTTER = 10  # row-label gutter before the bar's left border
MAX_LANES = 3  # label lanes per side

Seg = dict  # {"t": str, "role": str | None, "no": int | None}
Row = list  # list[Seg]

# semantic role → Style method; the ctxr-live mod maps the same roles to its own colors
_PAINT = {"load": "cyan", "action": "yellow", "compaction": "magenta",
          "occupancy": "green", "dim": "dim", "bold": "bold"}


def _seg(t: str, role: str | None = None, no: int | None = None) -> Seg:
    return {"t": t, "role": role, "no": no}


def _arrow_row(cols: set[int], symbol: str, width: int) -> str:
    return "".join(symbol if i in cols else " " for i in range(width)).rstrip()


def _label_lanes(events: list[tuple[int, int]], width: int) -> list[list[tuple[int, str, int]]]:
    """Greedy lane allocation of `str(no)` labels anchored at their arrow column.

    events = [(col, timeline_no)]; a label needs one space of gap to the previous label
    in its lane so adjacent numbers never fuse. Returns, per lane, the placed
    (col, label text clipped to the bar, no) in column order.
    """
    lanes: list[list[tuple[int, str, int]]] = []
    ends: list[int] = []  # first free column per lane
    for b, no in sorted(events):
        lbl = str(no)
        for li in range(len(lanes)):
            if b >= ends[li]:
                break
        else:
            if len(lanes) >= MAX_LANES:
                continue  # dropped; neighbors still anchor the area
            lanes.append([])
            ends.append(0)
            li = len(lanes) - 1
        lanes[li].append((b, lbl[: width - b], no))
        ends[li] = b + len(lbl) + 1
    return lanes


def _lane_segs(lane: list[tuple[int, str, int]], role: str) -> Row:
    segs: Row = []
    pos = 0
    for b, lbl, no in lane:
        if b > pos:
            segs.append(_seg(" " * (b - pos)))
        segs.append(_seg(lbl, role, no))
        pos = b + len(lbl)
    return segs


def _block_segs(cells: dict[int, int], compactions: set[int], max_est: int,
                role: str, width: int) -> Row:
    """One event lane: block height = √(est/max) over 8 levels; ⟐ cuts through."""
    segs: Row = []
    for i in range(width):
        if i in compactions:
            segs.append(_seg("⟐", "compaction"))
        elif i in cells:
            e = cells[i]
            lvl = 1 if max_est <= 0 or e <= 0 else max(
                1, round(math.sqrt(e / max_est) * (len(FILL) - 1)))
            segs.append(_seg(FILL[lvl], role))
        else:
            segs.append(_seg(" "))
    return segs


def window_scale(samples: list[dict], window_tokens: int) -> int:
    """Occupancy denominator: the configured window, snapped up to a known tier (or the
    peak itself) when a sample proves a bigger window."""
    peak = max((s["tokens"] for s in samples), default=0)
    if peak > window_tokens:
        return next((t for t in WINDOW_TIERS if t >= peak), peak)
    return window_tokens


def context_map_parts(timeline: list[dict], samples: list[dict], width: int = WIDTH,
                      window_tokens: int = 200_000) -> tuple[list[Row], Row | None]:
    """Map rows (legend → label lanes → event box → occupancy bar) and the time-axis row
    (None without reliable timestamps). Empty timeline → ([], None)."""
    # the map draws the session's own window; sidechain rows belong to a subagent's window
    # (they stay in the timeline listing, tagged) — mirrors the context_samples exclusion
    # number against the FULL timeline first: the listing (render_timeline_lines) numbers
    # every row, sidechain ones included, and the labels must point at those rows
    numbered = [(no, e) for no, e in enumerate(timeline, 1) if not e.get("sidechain")]
    timeline = [e for _, e in numbered]
    if not timeline:
        return [], None
    # one shared column mapping so the occupancy bar lines up with the event bar
    refs = sorted({e["evidence_ref"] for e in timeline if e.get("evidence_ref") is not None}
                  | {s["idx"] for s in samples})
    if not refs:
        return [], None
    # event rank → bar column (uniform spacing of displayed events)
    last_rank = max(1, len(refs) - 1)
    col = {idx: min(width - 1, rank * (width - 1) // last_rank)
           for rank, idx in enumerate(refs)}

    loads: list[tuple[int, int]] = []  # (col, timeline row number)
    acts: list[tuple[int, int]] = []
    load_cells: dict[int, int] = {}  # col → Σ est tokens landing there
    act_cells: dict[int, int] = {}
    compactions: set[int] = set()
    for no, e in numbered:
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
    head = [_seg("  "), _seg("context window map", "bold"), _seg(" (", "dim"),
            _seg("▼", "load"), _seg(" injected · ", "dim"), _seg("▲", "action"),
            _seg(" actions · ", "dim"), _seg("⟐", "compaction"),
            _seg(" compaction · height ≈ tokens · # = timeline row)", "dim")]
    gut = " " * GUTTER
    rows: list[Row] = [head]
    rows.extend([_seg(gut + " "), *_lane_segs(lane, "load")]
                for lane in reversed(_label_lanes(loads, width)))
    rows.append([_seg(f"{'  loads':<{GUTTER}} "),
                 _seg(_arrow_row({b for b, _ in loads}, "▼", width), "load")])
    rows.append([_seg(gut), _seg("┌" + "─" * width + "┐", "dim")])
    rows.append([_seg(gut), _seg("│", "dim"),
                 *_block_segs(load_cells, compactions, max_est, "load", width),
                 _seg("│", "dim"), _seg(" "), _seg("▼", "load"), _seg(" injected", "dim")])
    rows.append([_seg(gut), _seg("│", "dim"),
                 *_block_segs(act_cells, compactions, max_est, "action", width),
                 _seg("│", "dim"), _seg(" "), _seg("▲", "action"), _seg(" actions", "dim")])
    rows.append([_seg(gut), _seg("└" + "─" * width + "┘", "dim")])
    rows.append([_seg(f"{'  actions':<{GUTTER}} "),
                 _seg(_arrow_row({b for b, _ in acts}, "▲", width), "action")])
    rows.extend([_seg(gut + " "), *_lane_segs(lane, "action")]
                for lane in _label_lanes(acts, width))

    # second bar: window occupancy (green, linear vs the model window, carried forward)
    if samples:
        peak = max(s["tokens"] for s in samples)
        scale = window_scale(samples, window_tokens)
        occupancy: dict[int, int] = {}
        for s in samples:
            b = col[s["idx"]]
            occupancy[b] = max(occupancy.get(b, 0), s["tokens"])
        parts: Row = []
        level = 0
        for i in range(width):
            if i in occupancy:
                level = max(1, round(occupancy[i] / scale * (len(FILL) - 1)))
            parts.append(_seg("⟐", "compaction") if i in compactions
                         else _seg(FILL[level], "occupancy"))
        rows.append([_seg(gut), _seg("┌" + "─" * width + "┐", "dim")])
        rows.append([_seg(f"{'  window':<{GUTTER}}"), _seg("│", "dim"), *parts,
                     _seg("│", "dim"),
                     _seg(f" occupancy · peak {_short(peak)}/{_short(scale)} tok", "dim")])
        rows.append([_seg(gut), _seg("└" + "─" * width + "┘", "dim")])

    axis = None
    t0 = _fmt_ts(timeline[0].get("ts"))
    t1 = _fmt_ts(timeline[-1].get("ts"))
    if "--:--:--" not in (t0, t1):
        axis = [_seg(gut), _seg(f"{t0}{'':<{width - len(t0) - len(t1) + 2}}{t1}", "dim")]
    return rows, axis


def context_map_rows(timeline: list[dict], samples: list[dict], width: int = WIDTH,
                     window_tokens: int = 200_000) -> list[Row]:
    rows, axis = context_map_parts(timeline, samples, width, window_tokens)
    return rows + ([axis] if axis else [])


def render_rows(rows: list[Row], style: Style | None = None) -> list[str]:
    """Paint segments by role; a line's trailing blanks are dropped (as before the split)."""
    style = style or Style()
    return ["".join(getattr(style, _PAINT[s["role"]])(s["t"]) if s["role"] else s["t"]
                    for s in row).rstrip()
            for row in rows]


def context_map_lines(timeline: list[dict], samples: list[dict],
                      style: Style | None = None,
                      window_tokens: int = 200_000, width: int = WIDTH) -> list[str]:
    """Render the map; empty timeline → no lines. samples may be empty (no usage data)."""
    return render_rows(context_map_rows(timeline, samples, width, window_tokens), style)
