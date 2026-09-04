"""ASCII visualization: pure stdlib character drawing, zero extra dependencies.

CJK/full-width characters count as 2 columns via east_asian_width (CJK label alignment risk).
"""

from __future__ import annotations

import unicodedata

BAR = "▇"


def display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def pad_to(s: str, width: int) -> str:
    """Pad with spaces to align by display width (CJK-safe)."""
    gap = width - display_width(s)
    return s + " " * max(0, gap)


def truncate_display(s: str, width: int) -> str:
    out = []
    w = 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - 1:
            out.append("…")
            break
        out.append(ch)
        w += cw
    return "".join(out)


def hbar_chart(items: list[tuple[str, float]], total_width: int = 72,
               value_fmt=lambda v: f"{v:,.0f}", bar_paint=None) -> list[str]:
    """Horizontal bar chart: `label  ▇▇▇▇▇  value`. items = [(label, value)].

    bar_paint: optional str→str colorizer applied to the bar segment only
    (after layout, so alignment is unaffected).
    """
    if not items:
        return []
    label_w = min(36, max(display_width(lbl) for lbl, _ in items))
    max_v = max(v for _, v in items) or 1
    bar_w = max(8, total_width - label_w - 12)
    lines = []
    for lbl, v in items:
        n = max(1 if v > 0 else 0, round(v / max_v * bar_w))
        bar = BAR * n or "·"
        if bar_paint:
            bar = bar_paint(bar)
        lines.append(
            f"{pad_to(truncate_display(lbl, label_w), label_w)}  {bar} {value_fmt(v)}"
        )
    return lines


FILL = " ▁▂▃▄▅▆▇█"  # 9 levels; same visual language as the context-map lanes


def daily_histogram(days: list[tuple[str, int]], height: int = 4,
                    total_width: int = 72, bar_paint=None) -> list[str]:
    """Daily-activity column chart: one column per day on a shared baseline,
    scaled to the busiest day, weekly date ticks. days = [(MM-DD, count)].

    Zero days keep their slot as a baseline `·` so gaps in the streak stay visible.
    """
    if not days:
        return []
    paint = bar_paint or (lambda s: s)
    max_v = max(c for _, c in days) or 1
    gutter = len(str(max_v))
    plot_w = max(8, total_width - gutter - 4)
    step = 2 if 1 + (len(days) - 1) * 2 <= plot_w else 1  # gap between bars when room allows
    shown = days
    if 1 + (len(shown) - 1) * step > plot_w:
        shown = shown[-((plot_w - 1) // step + 1):]

    eighths = [max(1, round(c / max_v * height * 8)) if c else 0 for _, c in shown]
    gap = " " * (step - 1)
    lines = []
    for row in range(height):  # top row first
        base = (height - 1 - row) * 8
        cells = []
        for e, (_, c) in zip(eighths, shown, strict=True):
            lvl = min(8, max(0, e - base))
            if lvl:
                cells.append(paint(FILL[lvl]))
            elif row == height - 1 and c == 0:
                cells.append("·")
            else:
                cells.append(" ")
        label = str(max_v) if row == 0 else ""
        lines.append(f"{label:>{gutter}} │ {gap.join(cells)}")

    ticks = list(range(0, len(shown), 7))
    axis = ["─"] * (2 + (len(shown) - 1) * step)
    for i in ticks:
        axis[1 + i * step] = "┬"
    lines.append(f"{'0':>{gutter}} └" + "".join(axis))
    label_row = [" "] * total_width
    for i in ticks:
        start = max(gutter + 2, gutter + 3 + i * step - 2)  # centered under the tick
        text = shown[i][0]
        if start + len(text) <= total_width:
            label_row[start:start + len(text)] = text
    lines.append("".join(label_row).rstrip())
    if len(shown) < len(days):
        lines.append(" " * (gutter + 3) + f"… showing last {len(shown)} of {len(days)} days")
    return lines


def _short(n: int) -> str:
    if n >= 1_000_000:
        return f"{round(n / 1_000_000, 1):g}M"
    if n >= 1000:
        return f"{round(n / 1000, 1):g}k"
    return str(n)
