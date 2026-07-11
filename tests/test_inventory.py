"""inventory tests (AC1): scan rules, static/dynamic classification, miss_when inference, refresh merge."""

import json

from context_render.inventory.scanner import (
    STATES,
    Component,
    load_manifest,
    merge_refresh,
    scan_components,
    write_manifest,
)
from context_render.inventory.tokens import estimate_tokens


def _local(comps):
    return {c.id: c for c in comps if c.provenance == "local"}


def _hook(comps, event):
    return next(c for c in comps if c.type == "hook" and c.hook_event == event)


def test_scan_sources_and_classification(fake_repo):
    comps = _local(scan_components(fake_repo))
    assert comps["claude-md:root"].context == "static"
    assert comps["claude-md:src-api"].context == "dynamic"  # subdirectory = dynamic (AC1)
    skill = comps["skill:db-migrate"]
    assert skill.tokens_meta_est > 0 and skill.tokens_body_est > 0
    assert comps["command:release"].context == "dynamic"
    assert comps["agent:code-reviewer"].context == "static"
    assert comps["mcp:playwright"].context == "static"
    hook = _hook(comps.values(), "PreToolUse")
    assert hook.id.startswith("hook:PreToolUse-Bash-")  # content digest, not list position
    assert hook.tokens_est == 0
    assert hook.miss_when == "git_commit"  # auto-inference tentative (§8)
    assert _hook(comps.values(), "PostToolUse").miss_when is None
    # states per the A2.2 matrix
    assert comps["skill:db-migrate"].states == ["registered", "loaded", "invoked"]
    assert comps["claude-md:root"].states == ["registered", "loaded"]


def test_namespaced_commands_get_distinct_ids(fake_repo):
    """Regression: same-leaf commands in different namespaces collapsed onto one stem-keyed
    name, so _dedupe kept them only apart by provenance suffix and attribution credited both."""
    cmds = fake_repo / ".claude" / "commands"
    (cmds / "frontend").mkdir()
    (cmds / "backend").mkdir()
    (cmds / "frontend" / "review.md").write_text("frontend review", encoding="utf-8")
    (cmds / "backend" / "review.md").write_text("backend review", encoding="utf-8")
    comps = _local(scan_components(fake_repo))
    assert comps["command:frontend:review"].name == "frontend:review"
    assert comps["command:backend:review"].name == "backend:review"
    assert comps["command:frontend:review"].path == ".claude/commands/frontend/review.md"
    # flat commands keep their plain stem
    assert "command:release" in comps


def test_manifest_roundtrip(fake_repo):
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    write_manifest(fake_repo, comps)
    loaded = load_manifest(fake_repo)
    assert {c.id for c in loaded} == {c.id for c in comps}
    hook = _hook(loaded, "PreToolUse")
    assert hook.miss_when == "git_commit"
    assert hook.hook_commands == ["lint.sh"]


def test_refresh_merge(fake_repo):
    old = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    skill = next(c for c in old if c.id == "skill:db-migrate")
    skill.notes = "user annotation"
    # simulate a file disappearing
    (fake_repo / ".claude" / "commands" / "release.md").unlink()
    # simulate an addition
    (fake_repo / ".claude" / "commands" / "deploy.md").write_text("deploy", encoding="utf-8")
    new = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    merged = merge_refresh(old, new)
    by_id = {c.id: c for c in merged}
    assert by_id["command:release"].missing is True  # not deleted, marked missing
    assert by_id["command:deploy"].missing is False  # new component appended
    assert by_id["skill:db-migrate"].notes == "user annotation"  # manual edit preserved


def test_refresh_merge_plugin_components_do_not_collide():
    """plugin members share path=None and one source, so the merge key must include name."""
    def plugin_skill(name):
        return Component(id=f"skill:{name}", type="skill", name=name, context="dynamic",
                         provenance="plugin", source="plugin:myplug", states=STATES["skill"])

    old = [plugin_skill("alpha"), plugin_skill("beta")]
    old[0].notes = "user annotation"
    new = [plugin_skill("alpha"), plugin_skill("beta"), plugin_skill("gamma")]
    merged = merge_refresh(old, new)
    assert sorted(c.id for c in merged) == ["skill:alpha", "skill:beta", "skill:gamma"]
    by_id = {c.id: c for c in merged}
    assert by_id["skill:alpha"].notes == "user annotation"
    assert not by_id["skill:alpha"].missing and not by_id["skill:beta"].missing


def test_hook_identity_survives_group_inserted_above(fake_repo):
    """Regression (AB review): hook identity derives from content, not list position —
    inserting a group above an existing one must not re-key it, or archived usage rows
    (irrecoverable once transcripts expire) silently transplant onto the newcomer."""
    old = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    old_hook = _hook(old, "PreToolUse")
    old_hook.notes = "user annotation"
    settings = fake_repo / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"]["PreToolUse"].insert(
        0, {"matcher": "Write", "hooks": [{"type": "command", "command": "audit.sh"}]}
    )
    settings.write_text(json.dumps(data), encoding="utf-8")
    new = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    merged = merge_refresh(old, new)
    survivor = next(c for c in merged if c.id == old_hook.id)
    assert survivor.missing is False  # identity survived the insertion
    assert survivor.notes == "user annotation"  # annotations stay attached
    assert survivor.hook_commands == ["lint.sh"]
    newcomer = next(c for c in merged if c.hook_commands == ["audit.sh"])
    assert newcomer.id != old_hook.id and newcomer.missing is False


def test_identical_hook_groups_get_distinct_ids(fake_repo):
    settings = fake_repo / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"]["PreToolUse"].append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "lint.sh"}]}
    )
    settings.write_text(json.dumps(data), encoding="utf-8")
    hooks = [c for c in scan_components(fake_repo)
             if c.type == "hook" and c.hook_event == "PreToolUse" and c.provenance == "local"]
    assert len(hooks) == 2
    assert len({c.id for c in hooks}) == 2  # deterministic tiebreak, no collision


def test_token_estimate_cjk():
    # bytes/4 (utf-8): CJK chars are 3 bytes each; error is always marked "estimated".
    # CJK literals are intentional here — this verifies the CJK byte-width path.
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("中文字") == 3  # 9 bytes / 4 → ceil = 3
