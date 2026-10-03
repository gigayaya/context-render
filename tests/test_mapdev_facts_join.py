"""mapdev/facts_join.py — attribute observed search cost to files via chain_read facts.

Heuristic by construction: a chain_read's raw is the read path, its key links to the
search facts whose tokens_est we sum. Unmappable raws are skipped — prefer a miss over a
wrong hit."""

from context_render.mapdev.facts_join import join_facts


def _search(key, tokens, sid="s1", idx=0):
    return {"session_id": sid, "idx": idx, "kind": "search", "key": key, "raw": key,
            "tool": "grep", "tokens_est": tokens, "occupancy_est": None, "sidechain": 0,
            "confidence": "exact"}


def _chain(key, raw, sid="s1", idx=1):
    # raw is repo-relative (facts extractor v3) — the joinable form
    return {"session_id": sid, "idx": idx, "kind": "chain_read", "key": key, "raw": raw,
            "tool": "Read", "tokens_est": 0, "occupancy_est": None, "sidechain": 0,
            "confidence": "heuristic"}


def test_join_facts_multiple_sessions():
    rows = [
        _search("k1", 100, sid="s1"), _chain("k1", "pkg/a.py", sid="s1"),
        _search("k1", 200, sid="s2", idx=0), _chain("k1", "pkg/a.py", sid="s2"),
    ]
    per_file = join_facts(rows, {"pkg/a.py"})
    assert per_file["pkg/a.py"] == {"grep_count": 2, "tokens_est": 300}


def test_join_facts_skips_unmappable_raws():
    rows = [_search("x", 100), _chain("x", "/abs/elsewhere.py"),
            _search("y", 100, idx=2), _chain("y", "pkg/gone.py", idx=3)]
    assert join_facts(rows, {"pkg/a.py"}) == {}


def test_join_facts_chain_read_without_search_counts_once():
    rows = [_chain("solo", "pkg/a.py")]
    assert join_facts(rows, {"pkg/a.py"}) == {"pkg/a.py": {"grep_count": 1, "tokens_est": 0}}
