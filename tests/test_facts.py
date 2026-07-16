"""Self-derivation fact extraction (SPIKES.md W3).

The decontamination cases are the actual dry-run contamination samples (W3 #19), frozen
here as regressions; misses asserted below are deliberate (zero-false-positive priority).
"""

from __future__ import annotations

from context_render.attributor.facts import (
    MAPPING_KEY,
    canonical_key,
    extract_facts,
    split_alternation,
)
from context_render.parser.loader import Event, ParsedSession, parse_file
from tests.conftest import assistant, make_transcript, tool_result, tool_use, write_sidechain

CWD = "/repo"


def _parsed(lines, tmp_path, sidechains=()):
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(p, lines), encoding="utf-8")
    return parse_file(p, sidechains)


def _bash_session(commands_and_results, tmp_path, start=0):
    lines = []
    for i, (cmd, res) in enumerate(commands_and_results):
        tid = f"t{i}"
        lines.append(assistant(start + i * 2, CWD, [tool_use("Bash", {"command": cmd}, tid)]))
        lines.append(tool_result(start + i * 2 + 1, CWD, tid, res))
    return _parsed(lines, tmp_path)


def _facts(lines, tmp_path):
    return extract_facts(_parsed(lines, tmp_path)).facts


# ---- canonical normalization (§4.1) ----

def test_canonical_strips_regex_noise_and_case():
    assert canonical_key(r"\bRegister_Handler\b") == "registerhandler"
    assert canonical_key("retry.?policy{1,3}") == "retrypolicy"


def test_canonical_keeps_cjk():
    assert canonical_key("重試策略") == "重試策略"
    assert canonical_key(r"重試\s*策略") == "重試策略"


def test_canonical_short_fold_falls_back_to_raw():
    assert canonical_key("OK") == "ok"          # fold "ok" < 3 chars → strip+lower of the part
    assert canonical_key(" \\d+ ") == "\\d+"    # folds to "" → original strip+lower


def test_alternation_split():
    assert split_alternation("foo|bar") == ["foo", "bar"]
    assert split_alternation(r"foo\|bar") == ["foo", "bar"]
    assert split_alternation("plain") == ["plain"]


# ---- keyword detection: tool-call path and bash path (W3 #20) ----

def test_grep_tool_call_exact(tmp_path):
    lines = [
        assistant(0, CWD, [tool_use("Grep", {"pattern": "registerHandler"}, "t1")]),
        tool_result(1, CWD, "t1", "x" * 400),
    ]
    facts = _facts(lines, tmp_path)
    assert len(facts) == 1
    f = facts[0]
    assert (f.kind, f.key, f.tool, f.confidence) == ("search", "registerhandler", "Grep", "exact")
    assert f.raw == "registerHandler"
    assert f.tokens_est == 100  # ceil(400/4)


def test_glob_tool_call(tmp_path):
    lines = [
        assistant(0, CWD, [tool_use("Glob", {"pattern": "**/migrations/*.sql"}, "t1")]),
        tool_result(1, CWD, "t1", "a.sql"),
    ]
    facts = _facts(lines, tmp_path)
    assert facts[0].tool == "Glob" and facts[0].kind == "search"


def test_bash_search_heads(tmp_path):
    parsed = _bash_session([
        ("rg -n registerHandler src/", "src/a.py:1: hit"),
        ("git grep MIGRATION_TABLE", "db.py: MIGRATION_TABLE = ..."),
        ("fd -e py conftest", "tests/conftest.py"),
        ('find . -name "MIGRATION*"', "./db/migration_table.sql"),
    ], tmp_path)
    facts = extract_facts(parsed).facts
    by_tool = {f.tool: f for f in facts}
    assert by_tool["rg"].key == "registerhandler"
    assert by_tool["git grep"].key == "migrationtable"
    assert by_tool["fd"].key == "conftest"
    assert by_tool["find"].key == "migration"
    assert all(f.confidence == "exact" for f in facts)


def test_alternation_splits_tokens_evenly(tmp_path):
    lines = [
        assistant(0, CWD, [tool_use("Grep", {"pattern": "foo_x|bar_y"}, "t1")]),
        tool_result(1, CWD, "t1", "x" * 800),
    ]
    facts = _facts(lines, tmp_path)
    assert {f.key for f in facts} == {"foox", "bary"}
    assert [f.tokens_est for f in facts] == [100, 100]


def test_failed_search_still_counts_with_zero_tokens(tmp_path):
    lines = [
        assistant(0, CWD, [tool_use("Bash", {"command": "grep foo src/a.py"}, "t1")]),
        tool_result(1, CWD, "t1", "boom", is_error=True),
    ]
    facts = _facts(lines, tmp_path)
    assert len(facts) == 1 and facts[0].tokens_est == 0


# ---- decontamination regressions (W3 #19: real dry-run contamination samples) ----

def test_find_prune_produces_no_keyword(tmp_path):
    parsed = _bash_session([
        ("find . -path '*/node_modules*' -prune -o -type f -print", "./a\n./b"),
    ], tmp_path)
    assert extract_facts(parsed).facts == []


def test_find_prune_suppresses_its_name_values(tmp_path):
    parsed = _bash_session([
        ("find . -type d -name node_modules -prune -o -print", "./src\n./tests"),
    ], tmp_path)
    facts = extract_facts(parsed).facts
    # the -name value is exclusion machinery, not a keyword; structure walk still maps
    assert [f.kind for f in facts] == ["mapping"]
    assert facts[0].key == MAPPING_KEY


def test_find_not_name_is_exclusion_not_keyword(tmp_path):
    """Real contamination case from this repo's own history: a repo-layout walk whose
    -not -name/-not -path exclusions were being harvested as keywords."""
    cmd = ("find /repo -type d -not -path '*/.git/*' -not -path '*/.venv/*' "
           "-not -name '.git' -not -name '.venv' -not -path '*/__pycache__*' | sort")
    parsed = _bash_session([(cmd, "./src\n./tests")], tmp_path)
    facts = extract_facts(parsed).facts
    assert [f.kind for f in facts] == ["mapping"]  # layout walk with exclusions, no keywords
    assert facts[0].key == MAPPING_KEY


def test_alternation_raw_is_the_part(tmp_path):
    parsed = _bash_session([("grep -r 'foo_x|bar_y' src", "hit")], tmp_path)
    facts = extract_facts(parsed).facts
    assert {f.raw for f in facts} == {"foo_x", "bar_y"}


def test_grep_v_segment_excluded(tmp_path):
    parsed = _bash_session([
        ('grep -r foo_thing src | grep -v ".git/"', "src/a.py: foo_thing"),
    ], tmp_path)
    facts = extract_facts(parsed).facts
    assert [f.key for f in facts] == ["foothing"]


def test_pipeline_filter_grep_excluded(tmp_path):
    parsed = _bash_session([
        ("ps aux | grep python", "python ..."),
        ("cat log.txt | grep -r ERROR", "ERROR x"),  # recursive flag → still a search
        ("grep TODO src/main.py", "src/main.py: TODO"),  # has a path arg → search
    ], tmp_path)
    keys = [f.key for f in extract_facts(parsed).facts]
    assert "python" not in keys
    assert "error" in keys and "todo" in keys


def test_unbalanced_quotes_dropped(tmp_path):
    parsed = _bash_session([('grep "unclosed foo src/', "whatever")], tmp_path)
    assert extract_facts(parsed).facts == []


def test_grep_value_flag_not_mistaken_for_pattern(tmp_path):
    parsed = _bash_session([("grep -A 3 needle_x src/a.py", "hit")], tmp_path)
    facts = extract_facts(parsed).facts
    assert [f.key for f in facts] == ["needlex"]


# ---- repo layout detector (§4.2) ----

def test_find_type_d_is_mapping_exact(tmp_path):
    parsed = _bash_session([("find . -type d", "./src\n./tests")], tmp_path)
    facts = extract_facts(parsed).facts
    f = facts[0]
    assert (f.kind, f.key, f.confidence) == ("mapping", MAPPING_KEY, "exact")


def test_find_type_d_with_name_is_keyword_not_mapping(tmp_path):
    parsed = _bash_session([("find . -type d -name migrations", "./db/migrations")], tmp_path)
    facts = extract_facts(parsed).facts
    assert [f.kind for f in facts] == ["search"]
    assert facts[0].key == "migrations"


def test_tree_is_mapping(tmp_path):
    parsed = _bash_session([("tree -L 2", "src\ntests")], tmp_path)
    assert extract_facts(parsed).facts[0].kind == "mapping"


def test_ls_chain_within_one_call(tmp_path):
    parsed = _bash_session([("ls -la; ls src", "a b c")], tmp_path)
    f = extract_facts(parsed).facts[0]
    assert (f.kind, f.confidence) == ("mapping", "heuristic")


def test_consecutive_ls_calls_form_one_mapping(tmp_path):
    parsed = _bash_session([
        ("ls -la", "x" * 40),
        ("ls src", "y" * 40),
    ], tmp_path)
    facts = extract_facts(parsed).facts
    assert len(facts) == 1
    f = facts[0]
    assert (f.kind, f.confidence) == ("mapping", "heuristic")
    assert f.tokens_est == 20  # both calls' output counted


def test_single_ls_is_not_mapping(tmp_path):
    parsed = _bash_session([("ls -la", "a b")], tmp_path)
    assert extract_facts(parsed).facts == []


def test_ls_chain_broken_by_other_tool_call(tmp_path):
    lines = [
        assistant(0, CWD, [tool_use("Bash", {"command": "ls -la"}, "t1")]),
        tool_result(1, CWD, "t1", "a"),
        assistant(2, CWD, [tool_use("Read", {"file_path": "/repo/a.py"}, "t2")]),
        tool_result(3, CWD, "t2", "content"),
        assistant(4, CWD, [tool_use("Bash", {"command": "ls src"}, "t3")]),
        tool_result(5, CWD, "t3", "b"),
    ]
    assert _facts(lines, tmp_path) == []


# ---- chain_read (§4.3: both conditions or it doesn't count) ----

def _chain_lines(read_path, result_text, close_with_search=True):
    lines = [
        assistant(0, CWD, [tool_use("Grep", {"pattern": "retry_policy"}, "t1")]),
        tool_result(1, CWD, "t1", result_text),
        assistant(2, CWD, [tool_use("Read", {"file_path": read_path}, "t2")]),
        tool_result(3, CWD, "t2", "file content here"),
    ]
    if close_with_search:
        lines += [
            assistant(4, CWD, [tool_use("Grep", {"pattern": "retry_policy2"}, "t3")]),
            tool_result(5, CWD, "t3", "more"),
        ]
    return lines


def test_chain_read_merges_into_previous_search(tmp_path):
    facts = _facts(_chain_lines("/repo/src/retry.py", "src/retry.py:12: retry_policy"), tmp_path)
    chain = [f for f in facts if f.kind == "chain_read"]
    assert len(chain) == 1
    f = chain[0]
    assert f.key == "retrypolicy"  # the previous search's canonical key
    assert f.confidence == "heuristic" and f.tool == "Read"


def test_chain_read_requires_path_in_search_result(tmp_path):
    facts = _facts(_chain_lines("/repo/src/other.py", "src/retry.py:12: retry_policy"), tmp_path)
    assert [f for f in facts if f.kind == "chain_read"] == []


def test_chain_read_requires_closing_search(tmp_path):
    facts = _facts(
        _chain_lines("/repo/src/retry.py", "src/retry.py:12: retry_policy",
                     close_with_search=False),
        tmp_path,
    )
    assert [f for f in facts if f.kind == "chain_read"] == []


# ---- occupancy (§5) ----

def test_occupancy_counts_remaining_assistant_turns(tmp_path):
    lines = [
        assistant(0, CWD, [tool_use("Grep", {"pattern": "needle_x"}, "t1")]),
        tool_result(1, CWD, "t1", "x" * 40),  # 10 tokens
        assistant(2, CWD, [], usage=None),
        assistant(3, CWD, [], usage=None),
    ]
    f = _facts(lines, tmp_path)[0]
    assert f.tokens_est == 10
    assert f.occupancy_est == 20  # 2 assistant turns after the search


def test_occupancy_cut_at_compaction_boundary(tmp_path):
    from tests.conftest import _line

    lines = [
        assistant(0, CWD, [tool_use("Grep", {"pattern": "needle_x"}, "t1")]),
        tool_result(1, CWD, "t1", "x" * 40),
        assistant(2, CWD, [], usage=None),
        _line(3, "system", CWD, subtype="compact_boundary"),
        assistant(4, CWD, [], usage=None),  # beyond the boundary: doesn't count
    ]
    f = _facts(lines, tmp_path)[0]
    assert f.occupancy_est == 10  # 1 turn × 10 tokens


def test_occupancy_unknown_turns_left_empty():
    """A window with no assistant turns at all can't be measured — None, not 0/guessed."""
    events = [
        Event(idx=0, kind="tool_use", tool_name="Grep",
              tool_input={"pattern": "needle_x"}, tool_use_id="t1"),
        Event(idx=1, kind="tool_result", tool_use_id="t1", text="x" * 40),
    ]
    parsed = ParsedSession(session_id="s", path="p", events=events)
    f = extract_facts(parsed).facts[0]
    assert f.tokens_est == 10
    assert f.occupancy_est is None


# ---- sidechain (§4.4: costs count, windows stay separate) ----

def test_sidechain_facts_counted_with_own_window(tmp_path, fake_repo):
    main = [
        assistant(0, CWD, [tool_use("Agent", {"subagent_type": "explorer",
                                              "prompt": "go"}, "t1")]),
        tool_result(9, CWD, "t1", "done"),
        assistant(10, CWD, [], usage=None),
        assistant(11, CWD, [], usage=None),
        assistant(12, CWD, [], usage=None),
    ]
    side = [
        assistant(2, CWD, [tool_use("Bash", {"command": "grep -r side_needle src"}, "s1")]),
        tool_result(3, CWD, "s1", "x" * 40),
        assistant(4, CWD, [], usage=None),
    ]
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, main), encoding="utf-8")
    sp = write_sidechain(p, "a1", side, agent_type="explorer")
    parsed = parse_file(p, [sp])
    facts = extract_facts(parsed).facts
    f = next(f for f in facts if f.kind == "search")
    assert f.sidechain is True
    assert f.key == "sideneedle"
    # W2 window separation: occupancy uses the sidechain's own turn range (1 assistant
    # turn follows in its file), not the main chain's 3 later turns
    assert f.occupancy_est == f.tokens_est * 1


def test_tool_output_denominator_sums_all_results(tmp_path):
    parsed = _bash_session([
        ("grep needle_x src/a.py", "x" * 40),
        ("echo hi", "y" * 40),
    ], tmp_path)
    ext = extract_facts(parsed)
    assert ext.tool_output_tokens_est == 20  # every tool_result counts, search or not


def test_same_call_duplicate_keys_merge(tmp_path):
    lines = [
        assistant(0, CWD, [tool_use("Grep", {"pattern": "foo_x|FOO_X"}, "t1")]),
        tool_result(1, CWD, "t1", "x" * 80),
    ]
    facts = _facts(lines, tmp_path)
    assert len(facts) == 1  # both alternation parts fold to one canonical → one PK row
    assert facts[0].tokens_est == 20
