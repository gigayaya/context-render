"""Test fixtures: synthetic repo + synthetic transcript (shapes based on real observations in SPIKES.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

BASE_TS = "2026-07-11T14:{m:02d}:{s:02d}.000Z"


def _line(idx: int, type_: str, cwd: str, **kw) -> dict:
    obj = {
        "type": type_,
        "uuid": f"u-{idx}",
        "parentUuid": f"u-{idx-1}" if idx else None,
        "timestamp": BASE_TS.format(m=idx // 60, s=idx % 60),
        "cwd": cwd,
        "version": "2.1.207",
        "gitBranch": "main",
        "sessionId": "s",
        "isSidechain": False,
    }
    obj.update(kw)
    return obj


def make_transcript(repo: Path, lines: list[dict]) -> str:
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n"


def user_text(idx, cwd, text, **kw):
    return _line(idx, "user", cwd, message={"role": "user", "content": text}, **kw)


def assistant(idx, cwd, blocks, usage=None, model="claude-opus-4-8"):
    msg = {"role": "assistant", "model": model, "content": blocks}
    if usage:
        msg["usage"] = usage
    return _line(idx, "assistant", cwd, message=msg)


def tool_use(name, input_, tid):
    return {"type": "tool_use", "id": tid, "name": name, "input": input_}


def tool_result(idx, cwd, tid, content, is_error=False):
    return _line(
        idx, "user", cwd,
        message={"role": "user",
                 "content": [{"type": "tool_result", "tool_use_id": tid,
                              "content": content, "is_error": is_error}]},
    )


USAGE = {"input_tokens": 1000, "cache_creation_input_tokens": 2000,
         "cache_read_input_tokens": 7000, "output_tokens": 500}


def write_sidechain(session_file: Path, agent_id: str, lines: list[dict],
                    agent_type: str | None = None) -> Path:
    """Write a subagent transcript in the on-disk layout observed in SPIKES.md W2:
    <proj>/<session-id>/subagents/agent-<id>.jsonl, every line isSidechain+agentId,
    plus the sibling .meta.json when agent_type is given."""
    sc_dir = session_file.parent / session_file.stem / "subagents"
    sc_dir.mkdir(parents=True, exist_ok=True)
    stamped = [{**ln, "isSidechain": True, "agentId": agent_id} for ln in lines]
    path = sc_dir / f"agent-{agent_id}.jsonl"
    path.write_text(make_transcript(session_file, stamped), encoding="utf-8")
    if agent_type is not None:
        (sc_dir / f"agent-{agent_id}.meta.json").write_text(
            json.dumps({"agentType": agent_type, "description": "", "spawnDepth": 1}),
            encoding="utf-8",
        )
    return path


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Synthetic user repo: root/subdirectory CLAUDE.md, skill, command, agent, hook, mcp, file."""
    repo = tmp_path / "myproj"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# Project conventions\n" + "Rule. " * 100, encoding="utf-8")
    sub = repo / "src" / "api"
    sub.mkdir(parents=True)
    (sub / "CLAUDE.md").write_text("# API subdirectory conventions\ncontent", encoding="utf-8")
    skill_dir = repo / ".claude" / "skills" / "db-migrate"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: db-migrate\ndescription: database migration skill\n---\nbody content " * 20,
        encoding="utf-8",
    )
    cmd_dir = repo / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "release.md").write_text("release command content", encoding="utf-8")
    agents_dir = repo / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "code-reviewer.md").write_text("---\nname: code-reviewer\n---\nreview", encoding="utf-8")
    (repo / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command",
                                                       "command": "lint.sh"}]}
                    ],
                    "PostToolUse": [
                        {"matcher": "Edit", "hooks": [{"type": "command",
                                                       "command": "fmt.sh"}]}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"playwright": {"command": "npx playwright-mcp"}}}),
        encoding="utf-8",
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "conventions.md").write_text("conventions doc", encoding="utf-8")
    guidelines = repo / "guidelines"
    guidelines.mkdir()
    (guidelines / "INDEX.md").write_text("# Guidelines index", encoding="utf-8")
    return repo


@pytest.fixture
def rich_session_lines(fake_repo: Path):
    """Synthetic session covering every type × every state (incl. compaction, git commit, MISS scenarios)."""
    cwd = str(fake_repo)
    lines = [
        _line(0, "file-history-snapshot", cwd, messageId="m0", snapshot={}),
        # command invoked (exact)
        user_text(1, cwd, "<command-name>/release</command-name>\n"
                          "<command-message>release</command-message>"),
        # skill L: expansion event (exact)
        user_text(2, cwd, f"Base directory for this skill: {fake_repo}/.claude/skills/db-migrate"),
        # skill I: Skill tool_use + result is not error (exact)
        assistant(3, cwd, [tool_use("Skill", {"skill": "db-migrate"}, "t1")], usage=USAGE),
        tool_result(4, cwd, "t1", "completed"),
        # subagent I (exact)
        assistant(5, cwd, [tool_use("Task", {"subagent_type": "code-reviewer",
                                             "prompt": "review"}, "t2")], usage=USAGE),
        tool_result(6, cwd, "t2", "done"),
        # mcp I (exact, server level; per-tool into evidence)
        assistant(7, cwd, [tool_use("mcp__playwright__click", {"sel": "#a"}, "t3")]),
        tool_result(8, cwd, "t3", "clicked"),
        assistant(9, cwd, [tool_use("mcp__playwright__snapshot", {}, "t4")]),
        tool_result(10, cwd, "t4", "ok"),
        # file L: Read exact
        assistant(11, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/docs/conventions.md"}, "t5")]),
        tool_result(12, cwd, "t5", "content"),
        # subdirectory CLAUDE.md: directory activity heuristic
        assistant(13, cwd, [tool_use("Edit", {"file_path": f"{fake_repo}/src/api/main.py",
                                              "old_string": "a", "new_string": "b"}, "t6")]),
        tool_result(14, cwd, "t6", "ok"),
        # compaction (spike #11 known form, self-made fixture)
        _line(15, "system", cwd, subtype="compact_boundary"),
        # hook: PostToolUse triggered (attachment hook_success)
        _line(16, "attachment", cwd,
              attachment={"type": "hook_success", "hookName": "PostToolUse:Edit",
                          "hookEvent": "PostToolUse", "content": ""}),
        # Bash git commit (for MISS: PreToolUse hook not triggered)
        assistant(17, cwd, [tool_use("Bash", {"command": "git add -A && git commit -m 'feat: x'"}, "t7")]),
        tool_result(18, cwd, "t7", "[main abc1234] feat: x\n 2 files changed"),
        # Bash file-read heuristic
        assistant(19, cwd, [tool_use("Bash", {"command": "cat docs/conventions.md"}, "t8")]),
        tool_result(20, cwd, "t8", "conventions doc"),
        # file load for a non-manifest component (file_loads order observation)
        assistant(21, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/guidelines/INDEX.md"}, "t9")]),
        tool_result(22, cwd, "t9", "# Guidelines index"),
        # Read of a manifest component file (file_loads component-link observation)
        assistant(23, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/src/api/CLAUDE.md"}, "t10")]),
        tool_result(24, cwd, "t10", "# API subdirectory conventions"),
        _line(25, "summary", cwd, summary="Fix retry UI for login page timeout"),
    ]
    return lines


@pytest.fixture
def fake_projects(tmp_path: Path, fake_repo: Path, rich_session_lines) -> Path:
    """Synthetic ~/.claude/projects directory."""
    projects = tmp_path / "projects"
    proj_dir = projects / "-".join(str(fake_repo).split("/"))
    proj_dir.mkdir(parents=True)
    (proj_dir / "11111111-aaaa-bbbb-cccc-000000000001.jsonl").write_text(
        make_transcript(fake_repo, rich_session_lines), encoding="utf-8"
    )
    return projects
