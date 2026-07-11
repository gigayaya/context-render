"""attributor tests (AC2): every type × every state × exact/heuristic; MISS; monotonicity."""

from dataclasses import replace

from context_render.attributor import attribute
from context_render.inventory.scanner import scan_components
from context_render.parser import parse_file
from tests.conftest import (
    USAGE,
    assistant,
    make_transcript,
    tool_result,
    tool_use,
    user_text,
)


def _attribute(tmp_path, fake_repo, lines, extra=None):
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, lines), encoding="utf-8")
    parsed = parse_file(p)
    comps = scan_components(fake_repo)
    # the test env looks only at local components (exclude the machine user's global/plugin noise)
    comps = [c for c in comps if c.provenance == "local"]
    comps += extra or []
    return attribute(parsed, comps, fake_repo), comps


def _plugin_twin(fake_repo, name, plugin):
    """A plugin component sharing a local one's name — scanner._dedupe suffixes the id and
    keeps both, so the attributor has to keep both too."""
    local = next(c for c in scan_components(fake_repo)
                 if c.provenance == "local" and c.name == name)
    return replace(local, id=f"{local.id}@plugin", provenance="plugin",
                   source=f"plugin:{plugin}", path=None)


def _agg(att, cid, state):
    return att.usages.get((cid, state))


def test_full_matrix(tmp_path, fake_repo, rich_session_lines):
    att, comps = _attribute(tmp_path, fake_repo, rich_session_lines)

    # skill: I (exact) + L (exact)
    assert _agg(att, "skill:db-migrate", "invoked").count == 1
    assert _agg(att, "skill:db-migrate", "invoked").confidence == "exact"
    assert _agg(att, "skill:db-migrate", "loaded").confidence == "exact"
    # command: I (exact)
    assert _agg(att, "command:release", "invoked").confidence == "exact"
    # subagent: I (exact)
    assert _agg(att, "agent:code-reviewer", "invoked").confidence == "exact"
    # mcp: server-level I, per-tool counts into evidence
    mcp = _agg(att, "mcp:playwright", "invoked")
    assert mcp.count == 2
    assert any("click" in e["summary"] for e in mcp.evidence)
    # hook: PostToolUse triggered (heuristic)
    assert any(cid.startswith("hook:PostToolUse-Edit-") and state == "invoked"
               for cid, state in att.usages)
    # claude_md root always L (exact); subdirectory directory activity (heuristic)
    assert _agg(att, "claude-md:root", "loaded").confidence == "exact"
    sub = _agg(att, "claude-md:src-api", "loaded")
    assert sub is not None and sub.confidence == "heuristic"
    # registered: every non-missing component is always R
    for c in comps:
        assert _agg(att, c.id, "registered") is not None
    # git commit event (for MISS)
    assert att.git_commit_count == 1
    # compaction
    assert att.compaction_count == 1
    # session statistics
    assert att.turns >= 1
    assert att.summary_text == "Fix retry UI for login page timeout"


def test_skill_invoked_requires_ok_result(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Skill", {"skill": "db-migrate"}, "t1")]),
        tool_result(1, cwd, "t1", "boom", is_error=True),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    assert _agg(att, "skill:db-migrate", "invoked") is None  # zero tolerance for false positives (AC2a)


def test_bash_read_error_result_not_counted(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Bash", {"command": "cat docs/conventions.md"}, "t1")]),
        tool_result(1, cwd, "t1", "No such file", is_error=True),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    assert _agg(att, "file:docs/conventions.md", "loaded") is None


def test_plugin_qualified_skill_name(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Skill", {"skill": "someplugin:db-migrate"}, "t1")]),
        tool_result(1, cwd, "t1", "done"),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    assert _agg(att, "skill:db-migrate", "invoked") is not None


def test_command_prefix_fallback(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [user_text(0, cwd, "/release now please")]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    assert _agg(att, "command:release", "invoked") is not None


def _read_skill_md_lines(fake_repo):
    cwd = str(fake_repo)
    skill_md = f"{fake_repo}/.claude/skills/db-migrate/SKILL.md"
    return [
        assistant(0, cwd, [tool_use("Read", {"file_path": skill_md}, "t1")]),
        tool_result(1, cwd, "t1", "---\nname: db-migrate\n---\nbody"),
    ]


def test_read_skill_md_credits_skill_loaded(tmp_path, fake_repo):
    """Regression: the documented degraded fallback (Read SKILL.md → skill L) crashed with
    AttributeError because the name index holds lists, and this branch passed the raw list
    to mark() instead of iterating it."""
    att, _ = _attribute(tmp_path, fake_repo, _read_skill_md_lines(fake_repo))
    agg = _agg(att, "skill:db-migrate", "loaded")
    assert agg is not None
    assert agg.confidence == "heuristic"


def test_read_skill_md_ambiguous_twin_credits_both(tmp_path, fake_repo):
    # same-named local + plugin skill: a bare SKILL.md path can't say which one, so both get L
    twin = _plugin_twin(fake_repo, "db-migrate", "toolbox")
    att, _ = _attribute(tmp_path, fake_repo, _read_skill_md_lines(fake_repo), extra=[twin])
    assert _agg(att, "skill:db-migrate", "loaded") is not None
    assert _agg(att, twin.id, "loaded") is not None


def test_namespaced_command_invokes_only_its_namespace(tmp_path, fake_repo):
    """Regression: same-leaf commands in different namespaces were both keyed on the stem,
    so invoking one credited the other too — hiding it from the deadweight list."""
    cmds = fake_repo / ".claude" / "commands"
    (cmds / "frontend").mkdir()
    (cmds / "backend").mkdir()
    (cmds / "frontend" / "review.md").write_text("frontend review", encoding="utf-8")
    (cmds / "backend" / "review.md").write_text("backend review", encoding="utf-8")
    cwd = str(fake_repo)
    lines = [user_text(0, cwd, "<command-name>/frontend:review</command-name>")]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    invoked = _agg(att, "command:frontend:review", "invoked")
    assert invoked is not None
    assert invoked.confidence == "exact"  # single candidate → no ambiguity downgrade
    assert _agg(att, "command:backend:review", "invoked") is None


def test_timeline_event_level_triples(tmp_path, fake_repo, rich_session_lines):
    att, _ = _attribute(tmp_path, fake_repo, rich_session_lines)
    kinds = [t.kind for t in att.timeline]
    assert kinds[0] == "session_start"
    assert kinds[-1] == "session_end"
    assert "compaction" in kinds
    transitions = [t for t in att.timeline if t.kind == "transition"]
    assert any(t.component_id == "skill:db-migrate" and t.state == "invoked"
               for t in transitions)
    # idx ordering (holds even under the ts-missing/out-of-order degrade)
    idxs = [t.idx for t in att.timeline]
    assert idxs == sorted(idxs)


def test_skill_prefix_without_path_does_not_crash(tmp_path, fake_repo):
    """a user_msg that is exactly the skill-expansion prefix must degrade, not IndexError."""
    lines = [user_text(0, str(fake_repo), "Base directory for this skill:")]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    assert _agg(att, "skill:db-migrate", "loaded") is None


def test_caveat_not_captured_as_digest(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [
        user_text(0, cwd, "Caveat: the messages below were generated by the user "
                          "while running local commands."),
        user_text(1, cwd, "fix the login timeout bug"),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    assert att.prompt_digest == "fix the login timeout bug"
    assert att.turns == 1  # the caveat preamble is not a user turn


def test_multiedit_action_and_dir_activity(tmp_path, fake_repo):
    """MultiEdit edits must produce an action row and trigger the subdirectory CLAUDE.md heuristic."""
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("MultiEdit",
                                    {"file_path": f"{fake_repo}/src/api/main.py",
                                     "edits": [{"old_string": "a", "new_string": "b"}]}, "t1")]),
        tool_result(1, cwd, "t1", "ok"),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    assert any(t.kind == "action" and t.component_type == "MultiEdit" for t in att.timeline)
    sub = _agg(att, "claude-md:src-api", "loaded")
    assert sub is not None and sub.confidence == "heuristic"


def test_same_named_skills_do_not_collapse(tmp_path, fake_repo):
    """A local and a plugin skill may share a name. The expansion message carries the skill's
    base directory, which names the local one — the plugin twin must not swallow the credit."""
    twin = _plugin_twin(fake_repo, "db-migrate", "someplugin")
    lines = [user_text(0, str(fake_repo),
                       f"Base directory for this skill: {fake_repo}/.claude/skills/db-migrate")]
    att, _ = _attribute(tmp_path, fake_repo, lines, extra=[twin])
    local = _agg(att, "skill:db-migrate", "loaded")
    assert local is not None and local.confidence == "exact"
    assert _agg(att, "skill:db-migrate@plugin", "loaded") is None


def test_ambiguous_skill_name_credits_every_candidate_heuristically(tmp_path, fake_repo):
    """A bare name cannot say which same-named skill ran: credit both, and say it is a guess —
    better than crediting one arbitrarily and reporting the other as permanent deadweight."""
    twin = _plugin_twin(fake_repo, "db-migrate", "someplugin")
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Skill", {"skill": "db-migrate"}, "t1")], usage=USAGE),
        tool_result(1, cwd, "t1", "done"),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines, extra=[twin])
    for cid in ("skill:db-migrate", "skill:db-migrate@plugin"):
        agg = _agg(att, cid, "invoked")
        assert agg is not None and agg.count == 1
        assert agg.confidence == "heuristic"


def test_plugin_qualified_name_disambiguates_the_twin(tmp_path, fake_repo):
    """`plugin:skill` names the provenance, so the ambiguity resolves back to exact."""
    twin = _plugin_twin(fake_repo, "db-migrate", "someplugin")
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Skill", {"skill": "someplugin:db-migrate"}, "t1")]),
        tool_result(1, cwd, "t1", "done"),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines, extra=[twin])
    twin_agg = _agg(att, "skill:db-migrate@plugin", "invoked")
    assert twin_agg is not None and twin_agg.confidence == "exact"
    assert _agg(att, "skill:db-migrate", "invoked") is None


def test_attribution_skill_on_text_only_turn(tmp_path, fake_repo):
    """attributionSkill is the only L signal a skill turn that calls no tools ever emits;
    the kind dispatch must not `continue` past it."""
    line = assistant(0, str(fake_repo), [{"type": "text", "text": "guidance only"}], usage=USAGE)
    line["attributionSkill"] = "db-migrate"
    att, _ = _attribute(tmp_path, fake_repo, [line])
    agg = _agg(att, "skill:db-migrate", "loaded")
    assert agg is not None and agg.count == 1


def test_attribution_skill_does_not_double_count_expansion(tmp_path, fake_repo):
    """The expansion message may itself carry attributionSkill — one event, one L transition."""
    line = user_text(0, str(fake_repo),
                     f"Base directory for this skill: {fake_repo}/.claude/skills/db-migrate")
    line["attributionSkill"] = "db-migrate"
    att, _ = _attribute(tmp_path, fake_repo, [line])
    assert _agg(att, "skill:db-migrate", "loaded").count == 1
    rows = [t for t in att.timeline
            if t.component_id == "skill:db-migrate" and t.state == "loaded"]
    assert len(rows) == 1


def test_bash_read_nonexistent_file_not_recorded(tmp_path, fake_repo):
    """heuristic existence gate: result ok but file does not exist (e.g. a mis-split token) is not recorded."""
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Bash", {"command": "cat not-a-real-file.md"}, "t1")]),
        tool_result(1, cwd, "t1", "whatever output"),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    assert [f.path for f in att.file_reads if f.tool == "Bash"] == []


def test_agent_tool_marks_subagent_invoked(tmp_path, fake_repo):
    """SPIKES.md W2 #13: the dispatch tool is named Agent in current transcripts (Task in
    older ones); both must mark the subagent invoked."""
    cwd = str(fake_repo)
    lines = [
        assistant(1, cwd, [tool_use("Agent", {"subagent_type": "code-reviewer",
                                              "prompt": "go"}, "t1")], usage=USAGE),
        tool_result(2, cwd, "t1", "done"),
    ]
    att, _ = _attribute(tmp_path, fake_repo, lines)
    agg = _agg(att, "agent:code-reviewer", "invoked")
    assert agg is not None and agg.count == 1 and agg.confidence == "exact"
    assert "Agent(subagent_type=code-reviewer)" in agg.evidence[0]["summary"]


def test_sidechain_events_attributed_and_tagged(tmp_path, fake_repo):
    """subagent activity counts toward the session's component usage, with evidence
    tagged by agent; session stats (turns, digest) stay main-chain only."""
    from context_render.inventory.scanner import Component, scan_components
    from tests.conftest import write_sidechain

    cwd = str(fake_repo)
    main = [
        user_text(0, cwd, "run the review"),
        assistant(1, cwd, [tool_use("Agent", {"subagent_type": "code-reviewer",
                                              "prompt": "go"}, "t1")], usage=USAGE),
        tool_result(9, cwd, "t1", "verdict"),
    ]
    side = [
        user_text(2, cwd, "you are the reviewer"),
        user_text(3, cwd, f"Base directory for this skill: {fake_repo}/.claude/skills/db-migrate"),
        assistant(4, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/docs/conventions.md"},
                                    "s1")], usage=USAGE),
        tool_result(5, cwd, "s1", "conventions doc"),
    ]
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, main), encoding="utf-8")
    sp = write_sidechain(p, "a1", side, agent_type="code-reviewer")
    parsed = parse_file(p, [sp])
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    # file components are user-added to the manifest, never auto-scanned
    comps.append(Component(id="file:docs/conventions.md", type="file", name="conventions",
                           context="dynamic", provenance="local",
                           path="docs/conventions.md", states=["registered", "loaded"]))
    att = attribute(parsed, comps, fake_repo)

    # skill expanded inside the subagent → L exact, evidence names the agent
    skill = _agg(att, "skill:db-migrate", "loaded")
    assert skill is not None and skill.confidence == "exact"
    assert "[subagent:code-reviewer]" in skill.evidence[0]["summary"]
    # file read inside the subagent → L
    assert _agg(att, "file:docs/conventions.md", "loaded") is not None
    # session stats: the sidechain's user messages are not user turns
    assert att.turns == 1
    assert att.prompt_digest == "run the review"
    # sidechain usage is real spend → counted in the session total
    assert att.total_tokens == 2 * (1000 + 2000 + 7000 + 500)
    # file_reads carry the sidechain flag (report layer separates the windows)
    fr = [f for f in att.file_reads if f.path == "docs/conventions.md"]
    assert fr and fr[0].is_sidechain
    # timeline transitions from the sidechain are flagged too
    tl = [t for t in att.timeline if t.component_id == "skill:db-migrate" and t.state == "loaded"]
    assert tl and tl[0].is_sidechain


def test_plugin_qualified_subagent_type(tmp_path, fake_repo):
    """plugin agents are dispatched with a qualified subagent_type (plugin:name); the
    plugin half narrows the provenance (mirrors _skills_by_name / command fallback)."""
    from context_render.inventory.scanner import Component

    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Agent", {"subagent_type": "gigachang-skills:ab-review-con",
                                              "prompt": "go"}, "t1")], usage=USAGE),
        tool_result(1, cwd, "t1", "done"),
    ]
    extra = [Component(id="agent:ab-review-con", type="subagent", name="ab-review-con",
                       context="static", provenance="plugin",
                       source="plugin:gigachang-skills",
                       states=["registered", "invoked"])]
    att, _ = _attribute(tmp_path, fake_repo, lines, extra=extra)
    agg = _agg(att, "agent:ab-review-con", "invoked")
    assert agg is not None and agg.count == 1 and agg.confidence == "exact"


def test_qualified_subagent_narrows_same_named_local(tmp_path, fake_repo):
    """a local agent sharing the plugin agent's short name: the qualified dispatch must
    credit only the plugin one (single candidate → exact)."""
    from context_render.inventory.scanner import Component

    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Agent", {"subagent_type": "someplugin:code-reviewer",
                                              "prompt": "go"}, "t1")], usage=USAGE),
        tool_result(1, cwd, "t1", "done"),
    ]
    extra = [Component(id="agent:code-reviewer@plugin", type="subagent", name="code-reviewer",
                       context="static", provenance="plugin", source="plugin:someplugin",
                       states=["registered", "invoked"])]
    att, _ = _attribute(tmp_path, fake_repo, lines, extra=extra)
    plugin_agg = _agg(att, "agent:code-reviewer@plugin", "invoked")
    assert plugin_agg is not None and plugin_agg.confidence == "exact"
    assert _agg(att, "agent:code-reviewer", "invoked") is None  # local twin not credited
