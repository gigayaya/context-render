"""Context-window map: segment layer, byte-identical string layer, width, row numbering."""

import os
import time
from pathlib import Path

import pytest

from context_render.report.ansi import Style
from context_render.report.context_map import (
    context_map_lines,
    context_map_parts,
    context_map_rows,
    render_rows,
    window_scale,
)
from context_render.report.timeline import render_timeline_lines
from tests.test_report import _session_agg

GOLDEN = Path(__file__).parent / "fixtures" / "context_map_golden.txt"
TS = "2026-07-11T14:{:02d}:00+00:00"


def synthetic_timeline() -> list[dict]:
    """Every lane kind, a compaction, timestamps at both ends (axis drawn)."""
    return [
        {"kind": "session_start", "evidence_ref": -1, "est_tokens": 0, "ts": TS.format(0)},
        {"kind": "claude_md", "transition": "loaded", "evidence_ref": 0, "est_tokens": 739,
         "ts": None},
        {"kind": "file_read", "evidence_ref": 3, "est_tokens": 4200, "ts": TS.format(2)},
        {"kind": "action", "evidence_ref": 5, "est_tokens": 90, "ts": TS.format(3)},
        {"kind": "skill", "transition": "invoked", "evidence_ref": 7, "est_tokens": 1500,
         "ts": TS.format(4)},
        {"kind": "skill", "transition": "loaded", "evidence_ref": 8, "est_tokens": 2600,
         "ts": TS.format(4)},
        {"kind": "compaction", "evidence_ref": 12, "est_tokens": 0, "ts": TS.format(6)},
        {"kind": "file_read", "evidence_ref": 14, "est_tokens": 300, "ts": TS.format(7)},
        {"kind": "action", "evidence_ref": 15, "est_tokens": 20, "ts": TS.format(8)},
        {"kind": "session_end", "evidence_ref": 1_000_000_000, "est_tokens": 0,
         "ts": TS.format(9)},
    ]


SAMPLES = [{"idx": 3, "tokens": 40_000}, {"idx": 8, "tokens": 90_000},
           {"idx": 14, "tokens": 30_000}]


@pytest.fixture
def utc(monkeypatch):
    """The axis renders local time; pin it so the golden is machine-independent."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _golden_body(tmp_path, fake_repo, rich_session_lines) -> str:
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    sections = {
        "rich": context_map_lines(agg["timeline"], agg["context_samples"]),
        "synthetic": context_map_lines(synthetic_timeline(), SAMPLES),
        "synthetic-no-samples": context_map_lines(synthetic_timeline(), []),
        "rich-1m-window": context_map_lines(agg["timeline"], agg["context_samples"],
                                            window_tokens=1_000_000),
    }
    return "".join(f"=== {k}\n" + "\n".join(v) + "\n" for k, v in sections.items())


def test_map_lines_match_golden(tmp_path, fake_repo, rich_session_lines, utc):
    body = _golden_body(tmp_path, fake_repo, rich_session_lines)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(body, encoding="utf-8")
    assert body == GOLDEN.read_text(encoding="utf-8")


ROLES = {None, "load", "action", "compaction", "occupancy", "dim", "bold"}


def test_rows_are_segments_with_known_roles():
    rows = context_map_rows(synthetic_timeline(), SAMPLES)
    assert rows
    for row in rows:
        for seg in row:
            assert set(seg) == {"t", "role", "no"}
            assert seg["role"] in ROLES


def test_label_segments_carry_their_row_number():
    rows = context_map_rows(synthetic_timeline(), SAMPLES)
    labelled = [s for row in rows for s in row if s["no"] is not None]
    assert labelled, "load and action labels are placed"
    for s in labelled:
        assert s["t"] == str(s["no"])[: len(s["t"])]
        assert s["role"] in {"load", "action"}


def test_axis_is_split_from_map_rows(utc):
    rows, axis = context_map_parts(synthetic_timeline(), SAMPLES)
    assert axis is not None
    assert "14:00:00" in "".join(s["t"] for s in axis)
    assert context_map_rows(synthetic_timeline(), SAMPLES) == [*rows, axis]
    no_ts = [{**e, "ts": None} for e in synthetic_timeline()]
    assert context_map_parts(no_ts, SAMPLES)[1] is None


def test_render_rows_styled_strips_to_plain():
    import re

    rows = context_map_rows(synthetic_timeline(), SAMPLES)
    plain = render_rows(rows)
    styled = render_rows(rows, Style(True))
    assert any("\x1b[" in ln for ln in styled)
    assert [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in styled] == plain


def test_window_scale_snaps_to_tier():
    assert window_scale([], 200_000) == 200_000
    assert window_scale([{"idx": 0, "tokens": 150_000}], 200_000) == 200_000
    assert window_scale([{"idx": 0, "tokens": 612_887}], 200_000) == 1_000_000
    assert window_scale([{"idx": 0, "tokens": 1_500_000}], 200_000) == 1_500_000


def test_empty_timeline_with_samples_draws_nothing():
    assert context_map_parts([], [{"idx": 0, "tokens": 5}]) == ([], None)


def _row(kind, ref, detail, **kw):
    return {"ts": None, "component": None, "kind": kind, "transition": None,
            "detail": detail, "confidence": "exact", "evidence_ref": ref,
            "est_tokens": 10, "order": "ts", **kw}


def test_map_labels_use_full_timeline_numbers_with_sidechain():
    """A subagent row before a main-window read must not shift the read's label."""
    tl = [
        _row("session_start", -1, "cc 2.1.207"),
        _row("file_read", 2, "[subagent:x] Read b.md", sidechain=True),
        _row("file_read", 3, "Read a.md"),
    ]
    rows, _ = context_map_parts(tl, [])
    nos = [s["no"] for row in rows for s in row if s["no"] is not None]
    assert nos == [3]
    listing = render_timeline_lines(tl)
    assert listing[2].lstrip().startswith("3 ") and "a.md" in listing[2]
