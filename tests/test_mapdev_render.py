"""map_lines — merged map renderer (terminal + markdown share the builder)."""

from pathlib import Path

from context_render.config import Config
from context_render.guidance.refs import FileIndex
from context_render.mapdev.audit import aggregate_map


def map_agg(tmp_path: Path, files: dict[str, str], fact_rows=None) -> dict:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return aggregate_map(FileIndex(tmp_path), _fact_rows=fact_rows)


def render_map(agg: dict) -> str:
    from context_render.report.render_term import map_lines
    return "\n".join(map_lines(agg, Config(), full=False))


def _search(key, tokens):
    return {"session_id": "s1", "idx": 0, "kind": "search", "key": key, "raw": key,
            "tool": "grep", "tokens_est": tokens, "occupancy_est": None, "sidechain": 0,
            "confidence": "exact"}


def _chain(key, raw):
    return {"session_id": "s1", "idx": 1, "kind": "chain_read", "key": key, "raw": raw,
            "tool": "Read", "tokens_est": 0, "occupancy_est": None, "sidechain": 0,
            "confidence": "heuristic"}


def test_map_header_and_sections_in_order(tmp_path):
    out = render_map(map_agg(tmp_path, {
        "CLAUDE.md": "- `pkg/core.py` — core\n"
                     "- `notes.txt` — notes, not `gone.py`\n"
                     "Some architectural narrative here.\n",
        "pkg/core.py": "def a(): ...\n",
        "pkg/context_map.py": "def m1(): ...\n",
        "notes.txt": "",
    }, fact_rows=[_search("contextmap", 9000), _chain("contextmap", "pkg/context_map.py")]))
    assert out.startswith("Map — ")
    assert "files reachable from root CLAUDE.md" in out
    i_car = out.index("guidance carriers")
    i_str = out.index("structure:")
    i_dead = out.index("dead routes")
    i_cov = out.index("coverage:")
    i_note = out.index("measurements, not verdicts")
    assert i_car < i_str < i_dead < i_cov < i_note
    assert "auto-inject" in out and "prose 33%" in out
    assert "hop depth:" in out
    assert "gone.py" in out
    assert "py: 1/2" in out and "symbols: 1/2" in out
    assert "pkg/context_map.py" in out and "~9k tokens" in out
    assert "necessary, not sufficient" in out
    assert out.count("gone.py") == 1  # dead route printed exactly once


def test_map_missing_root_points_to_map_init(tmp_path):
    out = render_map(map_agg(tmp_path, {"a.py": ""}))
    assert "no root CLAUDE.md" in out
    assert "ctxr map init" in out


def test_map_degraded_note_when_not_joined(tmp_path):
    out = render_map(map_agg(tmp_path, {"CLAUDE.md": "- `a.py` — a\n", "a.py": "", "b.py": ""}))
    assert "no observed searches" in out
    assert "ctxr sync" in out


def test_map_grepped_but_reachable_marked_heuristic(tmp_path):
    out = render_map(map_agg(tmp_path, {"CLAUDE.md": "- `a.py` — a\n", "a.py": "def f(): ...\n"},
                             fact_rows=[_search("f", 500), _chain("f", "a.py")]))
    assert "wording-failure candidates" in out
    assert "~" in out.split("wording-failure candidates")[1].splitlines()[0]


def test_map_optional_structure_sections_only_when_non_empty(tmp_path):
    out = render_map(map_agg(tmp_path, {"CLAUDE.md": "- `a.py` — a\n", "a.py": ""}))
    for heading in ("beyond 3 hops", "no loading guarantee",
                    "imports that don't resolve in the repo",
                    "beyond the platform depth limit", "duplicate routes", "dead routes",
                    "stale references in referenced docs"):
        assert heading not in out, heading


def test_map_lazy_md_and_depth_rendered_under_structure(tmp_path):
    out = render_map(map_agg(tmp_path, {
        "CLAUDE.md": "- `docs/guide.md` — guide\n- `a.md` — one\n",
        "docs/guide.md": "# g\n",
        "a.md": "- `b.md` — two\n", "b.md": "- `c.md` — three\n",
        "c.md": "- `deep/leaf.py` — four\n", "deep/leaf.py": "",
    }))
    i_str = out.index("structure:")
    assert out.index("beyond 3 hops") > i_str
    assert out.index("no loading guarantee") > i_str
    assert "deep/leaf.py" in out and "docs/guide.md" in out


def test_map_dead_routes_split_carriers_from_referenced_docs(tmp_path):
    # a carrier's dead route is the map's own staleness; a stale ref inside a plain
    # referenced doc is an extract_refs artefact — heuristic, and marked ~
    out = render_map(map_agg(tmp_path, {
        "CLAUDE.md": "- `docs/x.md` — x\n- `lost.py` — lost\n",
        "docs/x.md": "see `nope.py`\n",
    }))
    i_carrier = out.index("dead routes (guidance carriers):")
    i_docs = out.index("stale references in referenced docs ~:")
    assert i_carrier < i_docs < out.index("coverage:")
    assert out.index("CLAUDE.md → 'lost.py'") < i_docs
    assert out.index("docs/x.md → 'nope.py'") > i_docs
    assert "~" in out[i_docs:].splitlines()[0]


def test_map_dead_routes_truncated_at_term_max(tmp_path):
    from context_render.report.render_term import MAP_TERM_MAX
    n = MAP_TERM_MAX + 2
    out = render_map(map_agg(tmp_path, {
        "CLAUDE.md": "".join(f"- `gone{i:02d}.py` — gone\n" for i in range(n)),
    }))
    rows = [ln for ln in out.splitlines() if "→ 'gone" in ln]
    assert len(rows) == MAP_TERM_MAX
    assert f"truncated {n - MAP_TERM_MAX} more (use --md for all)" in out


def test_map_term_and_md_dispatch(tmp_path):
    from context_render.report.render_md import render_md
    from context_render.report.render_term import render_term
    agg = map_agg(tmp_path, {"CLAUDE.md": "- `a.py` — alpha\n", "a.py": ""})
    assert render_term(agg, Config()).startswith("Map — ")
    md = render_md(agg, Config())
    assert md.startswith("# context-render — map")
    assert "auto-inject" in md
