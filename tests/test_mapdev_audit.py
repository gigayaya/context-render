"""mapdev/audit.py — merged map aggregation (carriers, structure, dead routes, coverage)."""

from pathlib import Path

import pytest

from context_render.guidance.refs import FileIndex, extract_refs
from context_render.mapdev.audit import aggregate_map


def write_files(tmp_path: Path, files: dict[str, str]) -> FileIndex:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return FileIndex(tmp_path)


def build(tmp_path: Path, files: dict[str, str]) -> dict:
    return aggregate_map(write_files(tmp_path, files))


def carrier(agg: dict, path: str) -> dict:
    hits = [c for c in agg["carriers"] if c["path"] == path]
    assert hits, f"{path} not audited: {[c['path'] for c in agg['carriers']]}"
    return hits[0]


def test_carriers_cover_root_and_dir_claude_mds_with_loading_kind(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "- `parser/loader.py` — loader\n",
        "parser/CLAUDE.md": "- `loader.py` — loader\n",
        "parser/loader.py": "x = 1\n",
    })
    assert agg["root_present"] is True
    assert carrier(agg, "CLAUDE.md")["loading"] == "auto-inject"
    assert carrier(agg, "parser/CLAUDE.md")["loading"] == "dir-entry"


def test_prose_share_counts_content_lines_only(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": ("# Map\n\n"
                      "- `a.py` — alpha rules\n"
                      "- `b.py` — beta rules\n"
                      "The platform follows a layered design.\n"
                      "Fees are decoupled through a registry.\n"),
        "a.py": "", "b.py": "",
    })
    c = carrier(agg, "CLAUDE.md")
    # content lines = 2 routing + 2 prose + 1 heading; blank excluded
    assert c["prose_share"] == pytest.approx(2 / 5)
    assert c["prose_lines"] == [5, 6]


def test_head_prose_counts_prose_before_first_routing_line(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": ("This preamble explains the architecture.\n"
                      "- `a.py` — alpha\n"
                      "Trailing note.\n"),
        "a.py": "",
    })
    assert carrier(agg, "CLAUDE.md")["head_prose"] == 1


def test_dead_route_reported_in_dead_routes(tmp_path):
    agg = build(tmp_path, {"CLAUDE.md": "- `gone/missing.py` — vanished\n"})
    assert agg["dead_routes"] == [{"carrier": "CLAUDE.md", "raw": "gone/missing.py"}]


def test_import_closure_is_audited_with_import_loading(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "@docs/map-part.md\n- `a.py` — alpha\n",
        "docs/map-part.md": "- `b.py` — beta\n",
        "a.py": "", "b.py": "",
    })
    assert carrier(agg, "docs/map-part.md")["loading"] == "import"


def test_bare_import_word_is_not_an_import(tmp_path):
    # prose mentioning "@import" (the syntax itself) must not register as an
    # external import — a real import target is path-shaped ("/" or ".")
    agg = build(tmp_path, {
        "CLAUDE.md": "carriers include the @import closure\n- `a.py` — alpha\n",
        "a.py": "",
    })
    assert agg["imports_external"] == []


def test_import_inside_code_span_ignored(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "uses `@docs/map-part.md` syntax\n- `a.py` — alpha\n",
        "docs/map-part.md": "- `b.py` — beta\n",
        "a.py": "", "b.py": "",
    })
    assert all(c["path"] != "docs/map-part.md" for c in agg["carriers"])


def test_import_inside_code_fence_ignored(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "```\n@docs/map-part.md\n```\n- `a.py` — alpha\n",
        "docs/map-part.md": "- `b.py` — beta\n",
        "a.py": "", "b.py": "",
    })
    assert all(c["path"] != "docs/map-part.md" for c in agg["carriers"])


def test_resident_tokens_sum_auto_inject_and_imports_only(tmp_path):
    from context_render.inventory.tokens import estimate_tokens
    files = {
        "CLAUDE.md": "@docs/map-part.md\n- `a.py` — alpha\n",
        "docs/map-part.md": "- `b.py` — beta\n",
        "parser/CLAUDE.md": "- `loader.py` — loader\n",
        "parser/loader.py": "", "a.py": "", "b.py": "",
    }
    agg = build(tmp_path, files)
    # independent expectation: root + import texts, dir-entry carrier excluded
    expected = estimate_tokens(files["CLAUDE.md"]) + estimate_tokens(files["docs/map-part.md"])
    assert agg["resident_tokens"] == expected
    assert carrier(agg, "parser/CLAUDE.md")["tokens_est"] > 0


def test_lazy_md_refs_lists_plain_referenced_docs(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "- `docs/guide.md` — reading guide\n- `a.py` — alpha\n",
        "docs/guide.md": "# guide\n",
        "a.py": "",
    })
    assert agg["lazy_md_refs"] == ["docs/guide.md"]


def test_duplicate_targets_within_one_carrier(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "- `a.py` — alpha\n- `a.py` — alpha again\n",
        "a.py": "",
    })
    assert {"carrier": "CLAUDE.md", "target": "a.py", "count": 2} in agg["duplicates"]


def test_self_reference_is_not_a_duplicate(tmp_path):
    # table rows saying "(own CLAUDE.md)" resolve layer-1 to the carrier itself;
    # a self-reference is not a route
    agg = build(tmp_path, {
        "CLAUDE.md": "- `sub/x.py` — x\n",
        "sub/CLAUDE.md": "see `CLAUDE.md`\nsee `CLAUDE.md` again\n- `x.py` — x\n",
        "sub/x.py": "",
    })
    assert agg["duplicates"] == []


def test_dotted_prose_tokens_are_not_external_imports(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "use @pytest.fixture as of @v1.2\n- `a.py` — alpha\n",
        "a.py": "",
    })
    assert agg["imports_external"] == []


def test_external_imports_home_prefixed_and_deduplicated(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "@~/.claude/x.md\n@~/.claude/x.md\n- `a.py` — alpha\n",
        "a.py": "",
    })
    assert agg["imports_external"] == ["@~/.claude/x.md"]


def test_import_cycle_terminates_and_audits_both(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "@docs/a.md\n- `x.py` — x\n",
        "docs/a.md": "@../CLAUDE.md\n- `y.py` — y\n",
        "x.py": "", "y.py": "",
    })
    assert "docs/a.md" in {c["path"] for c in agg["carriers"]}


def test_imports_beyond_depth_are_listed_not_lost(tmp_path):
    files = {"CLAUDE.md": "@docs/a1.md\n- `x.py` — x\n", "x.py": ""}
    for i in range(1, 6):
        files[f"docs/a{i}.md"] = f"@a{i + 1}.md\n"
    files["docs/a6.md"] = "leaf\n"
    agg = build(tmp_path, files)
    assert agg["imports_beyond_depth"] == ["docs/a6.md"]
    assert all(c["path"] != "docs/a6.md" for c in agg["carriers"])


def test_cross_carrier_repetition_is_not_a_duplicate(tmp_path):
    # every per-directory CLAUDE.md pointing at the same ledger file is how
    # per-dir guidance works — ambiguity is only repetition inside ONE routing table
    agg = build(tmp_path, {
        "CLAUDE.md": "- `a.py` — alpha\n",
        "sub/CLAUDE.md": "- `../a.py` — alpha again\n",
        "sub/keep.py": "",
        "a.py": "",
    })
    assert agg["duplicates"] == []


def test_depth_over3_lists_files_beyond_three_hops(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": "- `a.md` — level one\n",
        "a.md": "- `b.md` — level two\n",
        "b.md": "- `c.md` — level three\n",
        "c.md": "- `deep/leaf.py` — level four\n",
        "deep/leaf.py": "",
    })
    assert agg["depth"]["over3"] == ["deep/leaf.py"]
    assert agg["depth"]["max_hop"] == 4


def test_label_quality_bare_and_echo(tmp_path):
    agg = build(tmp_path, {
        "CLAUDE.md": ("- `parser/loader.py` — parser loader\n"
                      "- `a.md`\n"
                      "- `b.py` — chargeback late-fee rules\n"),
        "parser/loader.py": "", "a.md": "x\n", "b.py": "",
    })
    c = carrier(agg, "CLAUDE.md")
    assert c["bare_paths"] == 1     # `a.md` has no label at all
    assert c["label_echoes"] == 1   # "parser loader" restates the path


def test_missing_root_claude_md(tmp_path):
    agg = build(tmp_path, {"a.py": ""})
    assert agg["root_present"] is False
    assert agg["carriers"] == []


# ---- aggregate_map (merged report) ----

def _search(key, tokens, sid="s1", idx=0):
    return {"session_id": sid, "idx": idx, "kind": "search", "key": key, "raw": key,
            "tool": "grep", "tokens_est": tokens, "occupancy_est": None, "sidechain": 0,
            "confidence": "exact"}


def _chain(key, raw, sid="s1", idx=1):
    return {"session_id": sid, "idx": idx, "kind": "chain_read", "key": key, "raw": raw,
            "tool": "Read", "tokens_est": 0, "occupancy_est": None, "sidechain": 0,
            "confidence": "heuristic"}


COV_FILES = {
    "CLAUDE.md": "see `pkg/`",
    "pkg/CLAUDE.md": "`core.py` only",
    "pkg/core.py": "def a(): ...\ndef b(): ...\n",
    "pkg/context_map.py": "def m1(): ...\ndef m2(): ...\ndef m3(): ...\n",
    "broken.py": "def broken(:\n",
}


def test_map_report_type_and_static_counts(tmp_path):
    agg = aggregate_map(write_files(tmp_path, COV_FILES))
    assert agg["report_type"] == "map" and agg["schema_version"] == 1
    assert agg["root_present"] is True
    # reachable: CLAUDE.md, pkg/CLAUDE.md (via dir `pkg/` → ls children), pkg/core.py,
    # pkg/context_map.py (also a direct child of pkg/)
    assert agg["files"]["total"] == 5 and agg["files"]["reachable"] == 4
    assert agg["py"]["total"] == 3 and agg["py"]["reachable"] == 2
    assert agg["symbols"]["total"] == 5          # broken.py excluded from total
    assert agg["symbols"]["reachable"] == 5      # core.py 2 + context_map.py 3
    assert agg["parse_failed"] == 1
    assert agg["joined"] is False
    assert agg["window_label"] == "last 30d"
    assert "stale" not in agg["carriers"][0]     # per-carrier stale removed


def test_map_hop_dist_counts_all_files_not_only_py(tmp_path):
    agg = aggregate_map(write_files(tmp_path, {
        "CLAUDE.md": "- `docs/guide.md` — guide\n- `a.py` — alpha\n",
        "docs/guide.md": "# g\n", "a.py": "",
    }))
    # CLAUDE.md hop 0, docs/guide.md hop 1, a.py hop 1
    assert agg["depth"]["hop_dist"] == {0: 1, 1: 2}


def test_map_unreachable_py_sorted_by_observed_cost(tmp_path):
    index = write_files(tmp_path, {
        "CLAUDE.md": "- `pkg/core.py` — core\n",
        "pkg/core.py": "def a(): ...\n",
        "pkg/context_map.py": "def m1(): ...\ndef m2(): ...\ndef m3(): ...\n",
        "broken.py": "def broken(:\n",
    })
    rows = [_search("contextmap", 9000), _chain("contextmap", "pkg/context_map.py")]
    agg = aggregate_map(index, _fact_rows=rows)
    assert agg["joined"] is True
    top = agg["unreachable_py"][0]
    assert top["path"] == "pkg/context_map.py"
    assert top["defs"] == 3 and top["grep_count"] == 1 and top["tokens_est"] == 9000
    assert [r["path"] for r in agg["unreachable_py"]][-1] == "broken.py"
    assert all(r["path"].endswith(".py") for r in agg["unreachable_py"])


def test_map_grepped_but_reachable(tmp_path):
    index = write_files(tmp_path, {
        "CLAUDE.md": "- `pkg/core.py` — core\n", "pkg/core.py": "def a(): ...\n"})
    rows = [_search("corea", 500), _chain("corea", "pkg/core.py")]
    agg = aggregate_map(index, _fact_rows=rows)
    assert agg["grepped_but_reachable"] == [
        {"path": "pkg/core.py", "hop": 1, "grep_count": 1, "tokens_est": 500}]


def test_map_unmappable_chain_read_ignored(tmp_path):
    index = write_files(tmp_path, {
        "CLAUDE.md": "- `pkg/core.py` — core\n", "pkg/core.py": "def a(): ...\n",
        "pkg/other.py": "def o(): ...\n"})
    rows = [_search("x", 100), _chain("x", "/somewhere/else/other.py"),
            _search("y", 100, idx=2), _chain("y", str(tmp_path / "pkg" / "core.py"), idx=3)]
    agg = aggregate_map(index, _fact_rows=rows)
    assert all(r["grep_count"] == 0 for r in agg["unreachable_py"])
    assert agg["grepped_but_reachable"] == []


def test_join_from_real_store_ingest(fake_repo, fake_projects):
    """Regression (the v2 bug): sessions.path stores the TRANSCRIPT .jsonl path, not the
    repo root — the join must not lean on it. chain_read raws are repo-relative at ingest
    (extractor v3), so `aggregate_map` over a really-synced store attributes observed cost."""
    from context_render.config import Config
    from context_render.pipeline import scan_repo
    from context_render.store import Store
    from tests.test_analyze import _add_session, _prep, search_lines

    (fake_repo / "src" / "retry.py").write_text("def retry(): ...\n", encoding="utf-8")
    _prep(fake_repo)
    _add_session(fake_projects, fake_repo, "33333333-aaaa-bbbb-cccc-000000000003",
                 search_lines(fake_repo))
    scan_repo(fake_repo, Config(), projects_dir=fake_projects)

    store = Store(fake_repo / ".context-render" / "db.sqlite")
    try:
        agg = aggregate_map(FileIndex(fake_repo), store=store, since_iso=None)
    finally:
        store.close()
    assert agg["joined"] is True
    # fake_repo's root CLAUDE.md routes nowhere, so src/retry.py is unreachable —
    # this is where the join must land (verified, not assumed: grepped_but_reachable == [])
    assert agg["grepped_but_reachable"] == []
    row = next(r for r in agg["unreachable_py"] if r["path"] == "src/retry.py")
    assert row["grep_count"] >= 1 and row["tokens_est"] > 0


def test_map_no_store_degrades_gracefully(tmp_path):
    agg = aggregate_map(write_files(tmp_path, COV_FILES), store=None)
    assert agg["joined"] is False
    assert all(r["grep_count"] == 0 and r["tokens_est"] == 0 for r in agg["unreachable_py"])


def test_map_dead_routes_from_reach_stale(tmp_path):
    agg = aggregate_map(write_files(tmp_path, {
        "CLAUDE.md": "see `pkg/`",
        "pkg/CLAUDE.md": "`core.py`; old `gone.py`",
        "pkg/core.py": "",
    }))
    assert agg["dead_routes"] == [{"carrier": "pkg/CLAUDE.md", "raw": "gone.py"}]


def test_map_dead_routes_equal_per_carrier_extract_refs_without_imports(tmp_path):
    """Equivalence gate: the single source (reach.stale) must match the old
    per-carrier extract_refs pass on the dry-run edge cases — multi-segment relative ref from a
    subdirectory, and a ref into a runtime-artifact directory absent on a fresh clone."""
    index = write_files(tmp_path, {
        "CLAUDE.md": "- `attributor/rules.py` — rules\n- `tests/` — suite\n"
                     "- `.context-render/manifest.yaml` — inventory\n",
        "attributor/rules.py": "",
        "tests/CLAUDE.md": "- `attributor/rules.py` — rules again\n- `test_x.py` — x\n",
        "tests/test_x.py": "",
    })
    agg = aggregate_map(index)
    audited = {c["path"] for c in agg["carriers"]}
    old_style = set()
    for path in audited:
        text = (tmp_path / path).read_text()
        for s in extract_refs(text, path, index)[1]:
            old_style.add((path, s.raw))
    new_style = {(d["carrier"], d["raw"]) for d in agg["dead_routes"] if d["carrier"] in audited}
    assert new_style == old_style
    assert old_style == {("CLAUDE.md", ".context-render/manifest.yaml")}  # probe-verified


def test_map_dead_routes_include_plain_referenced_md_like_old_coverage(tmp_path):
    # reach.stale covers every .md the agent may follow, not only audited carriers —
    # old coverage's stale list did the same; the merged report keeps that superset
    agg = aggregate_map(write_files(tmp_path, {
        "CLAUDE.md": "- `docs/x.md` — x\n- `a.py` — a\n",
        "docs/x.md": "see `nope.py`\n", "a.py": "",
    }))
    assert {"carrier": "docs/x.md", "raw": "nope.py"} in agg["dead_routes"]
    assert "docs/x.md" not in {c["path"] for c in agg["carriers"]}


def test_map_imported_carrier_is_reach_start_and_its_dead_routes_count(tmp_path):
    agg = aggregate_map(write_files(tmp_path, {
        "CLAUDE.md": "@docs/map-part.md\n- `a.py` — alpha\n",
        "docs/map-part.md": "- `b.py` — beta\n- `../lost.py` — dead\n",
        "a.py": "", "b.py": "",
    }))
    assert agg["files"]["reachable"] == 4              # CLAUDE.md, docs/map-part.md, a.py, b.py
    assert agg["depth"]["hop_dist"].get(0) == 2        # both carriers are hop-0 starts
    assert {"carrier": "docs/map-part.md", "raw": "../lost.py"} in agg["dead_routes"]
    assert all(not d["raw"].startswith("@") for d in agg["dead_routes"])  # import ≠ dead route
    assert agg["imports_external"] == []


def test_map_unresolvable_import_stays_external_not_dead(tmp_path):
    agg = aggregate_map(write_files(tmp_path, {
        "CLAUDE.md": "@~/.claude/x.md\n- `a.py` — alpha\n", "a.py": ""}))
    assert agg["imports_external"] == ["@~/.claude/x.md"]
    assert agg["dead_routes"] == []


def test_map_fact_rows_change_only_cost_fields(tmp_path):
    index = write_files(tmp_path, COV_FILES)
    base = aggregate_map(index)
    joined = aggregate_map(index, _fact_rows=[
        _search("contextmap", 9000), _chain("contextmap", "pkg/context_map.py")],
        window_label="last 7d")
    static_keys = ["carriers", "resident_tokens", "lazy_md_refs", "imports_external",
                   "imports_beyond_depth", "duplicates", "depth", "dead_routes",
                   "files", "py", "symbols", "parse_failed"]
    for k in static_keys:
        assert base[k] == joined[k], k
    assert base["joined"] is False and joined["joined"] is True
    assert joined["window_label"] == "last 7d"


def test_import_of_an_existing_dir_carrier_is_not_a_dead_route(tmp_path):
    # root @imports a per-directory CLAUDE.md: the target is already audited as
    # dir-entry, so it never carries the "import" loading kind — the raw is still an
    # import, not a route
    agg = build(tmp_path, {
        "CLAUDE.md": "@pkg/CLAUDE.md\n- `a.py` — alpha\n",
        "pkg/CLAUDE.md": "- `core.py` — core\n",
        "pkg/core.py": "", "a.py": "",
    })
    assert agg["dead_routes"] == []
    assert carrier(agg, "pkg/CLAUDE.md")["loading"] == "dir-entry"


def test_import_cycle_back_to_root_is_not_a_dead_route(tmp_path):
    # the cycle target is root CLAUDE.md, already audited as auto-inject — same class of
    # bug as the dir-entry case. (`@../CLAUDE.md` is written root-relative here because
    # _IMPORT_RE does not match a dot-relative `@../` / `@./` token at all — a separate
    # gap in import extraction, not in the exclusion set.)
    agg = build(tmp_path, {
        "CLAUDE.md": "@docs/a.md\n- `x.py` — x\n",
        "docs/a.md": "@CLAUDE.md\n- `y.py` — y\n",
        "x.py": "", "y.py": "",
    })
    assert agg["dead_routes"] == []


def test_beyond_depth_import_is_listed_once_not_also_a_dead_route(tmp_path):
    files = {"CLAUDE.md": "@docs/a1.md\n- `x.py` — x\n", "x.py": ""}
    for i in range(1, 6):
        files[f"docs/a{i}.md"] = f"@a{i + 1}.md\n"
    files["docs/a6.md"] = "leaf\n"
    agg = build(tmp_path, files)
    assert agg["imports_beyond_depth"] == ["docs/a6.md"]
    assert agg["dead_routes"] == []  # reported under one heading, never two


def test_dead_routes_in_an_unreachable_carrier_are_still_measured(tmp_path):
    # nothing routes into orphan/, so build_reach never visits orphan/CLAUDE.md;
    # an audited carrier's own staleness is in scope whether or not it is reachable
    agg = build(tmp_path, {
        "CLAUDE.md": "- `a.py` — alpha\n- `lost.py` — lost\n",
        "orphan/CLAUDE.md": "- `vanished.py` — gone\n",
        "a.py": "",
    })
    assert {"carrier": "orphan/CLAUDE.md", "raw": "vanished.py"} in agg["dead_routes"]
    # the reachable carrier's rows are not double-counted by the extra pass
    assert [d for d in agg["dead_routes"] if d["carrier"] == "CLAUDE.md"] == [
        {"carrier": "CLAUDE.md", "raw": "lost.py"}]
