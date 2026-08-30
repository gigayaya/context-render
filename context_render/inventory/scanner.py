"""Scaffolding scan and manifest generation (A6).

The manifest is hand-editable and version-controlled — the scaffolding list is itself part of
the scaffolding. It is the asset you author; the DB is the asset you cannot re-derive (see
store/db.py: transcripts expire, its rows do not).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..errors import PreconditionError
from .tokens import estimate_tokens

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".context-render", "dist", "build"}

# _dedupe id suffixes (`@provenance` or `@provenance-N`), recognized when re-deriving a
# component name from an old manifest that predates the explicit `name` field
_DEDUPE_SUFFIX_RE = re.compile(r"@(?:local|global|plugin)(?:-\d+)?$")

STATES = {  # A2.2 three-state applicability matrix
    "skill": ["registered", "loaded", "invoked"],
    "command": ["registered", "invoked"],
    "subagent": ["registered", "invoked"],
    "mcp": ["registered", "invoked"],
    "hook": ["registered", "invoked"],
    "claude_md": ["registered", "loaded"],
    "file": ["registered", "loaded"],
}


@dataclass
class Component:
    id: str
    type: str  # skill|command|subagent|mcp|hook|claude_md|file
    name: str
    context: str  # static | dynamic
    provenance: str  # local | global | plugin
    path: str | None = None
    source: str | None = None
    states: list[str] = field(default_factory=list)
    tokens_est: int | None = None
    tokens_meta_est: int | None = None
    tokens_body_est: int | None = None
    miss_when: str | None = None
    missing: bool = False
    notes: str | None = None
    # hook-specific (for attribution matching)
    hook_event: str | None = None
    hook_matcher: str | None = None
    hook_commands: list[str] = field(default_factory=list)

    @property
    def static_tokens(self) -> int:
        """static total contribution: for skills, count only the metadata part (A6.1)."""
        if self.type == "skill":
            return self.tokens_meta_est or 0
        return self.tokens_est or 0 if self.context == "static" else 0

    def to_yaml_dict(self) -> dict:
        d: dict = {"id": self.id, "type": self.type, "name": self.name}
        if self.path:
            d["path"] = self.path
        if self.source:
            d["source"] = self.source
        d["context"] = self.context
        d["provenance"] = self.provenance
        d["states"] = list(self.states)
        if self.type == "skill":
            d["tokens_meta_est"] = self.tokens_meta_est or 0
            d["tokens_body_est"] = self.tokens_body_est or 0
        else:
            d["tokens_est"] = self.tokens_est or 0
        if self.miss_when:
            d["miss_when"] = self.miss_when
        if self.missing:
            d["missing"] = True
        if self.notes:
            d["notes"] = self.notes
        if self.hook_event:
            d["hook_event"] = self.hook_event
        if self.hook_matcher is not None:
            d["hook_matcher"] = self.hook_matcher
        if self.hook_commands:
            d["hook_commands"] = self.hook_commands
        return d

    @classmethod
    def from_yaml_dict(cls, d: dict) -> "Component":
        cid = str(d.get("id", ""))
        name = d.get("name")
        if not name:
            # manifests written before the explicit name field: re-derive from the id,
            # stripping the _dedupe provenance suffix — attribution matches by name
            # (rules._index), so `skill:x@plugin` must round-trip to name `x`, not `x@plugin`
            name = cid.split(":", 1)[1] if ":" in cid else cid
            name = _DEDUPE_SUFFIX_RE.sub("", name)
        return cls(
            id=cid,
            type=str(d.get("type", "file")),
            name=str(name),
            context=str(d.get("context", "dynamic")),
            provenance=str(d.get("provenance", "local")),
            path=d.get("path"),
            source=d.get("source"),
            states=list(d.get("states") or []),
            tokens_est=d.get("tokens_est"),
            tokens_meta_est=d.get("tokens_meta_est"),
            tokens_body_est=d.get("tokens_body_est"),
            miss_when=d.get("miss_when"),
            missing=bool(d.get("missing")),
            notes=d.get("notes"),
            hook_event=d.get("hook_event"),
            hook_matcher=d.get("hook_matcher"),
            hook_commands=list(d.get("hook_commands") or []),
        )


def _slug(rel_dir: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", rel_dir).strip("-") or "root"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _split_frontmatter(text: str) -> tuple[str, str]:
    """SKILL.md → (frontmatter/metadata, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[1], parts[2]
    return "", text


def _scan_skills(base: Path, provenance: str, source_prefix: str | None,
                 repo_root: Path) -> list[Component]:
    out = []
    skills_dir = base
    if not skills_dir.is_dir():
        return out
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name = skill_md.parent.name
        meta, body = _split_frontmatter(_read(skill_md))
        try:
            rel = str(skill_md.relative_to(repo_root))
        except ValueError:
            rel = str(skill_md)
        out.append(
            Component(
                id=f"skill:{name}", type="skill", name=name,
                context="dynamic",  # metadata=static, body=dynamic; tokens listed separately
                provenance=provenance,
                path=rel if provenance == "local" else None,
                source=f"{source_prefix}" if source_prefix else (None if provenance == "local" else str(skill_md)),
                states=STATES["skill"],
                tokens_meta_est=estimate_tokens(meta),
                tokens_body_est=estimate_tokens(body),
            )
        )
    return out


def _scan_commands(base: Path, provenance: str, source_prefix: str | None,
                   repo_root: Path) -> list[Component]:
    out = []
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*.md")):
        # subdirectories namespace the invocation (frontend/review.md → /frontend:review),
        # so the name must carry them — a bare stem would collapse same-leaf commands
        name = ":".join(f.relative_to(base).with_suffix("").parts)
        try:
            rel = str(f.relative_to(repo_root))
        except ValueError:
            rel = str(f)
        out.append(
            Component(
                id=f"command:{name}", type="command", name=name, context="dynamic",
                provenance=provenance,
                path=rel if provenance == "local" else None,
                source=source_prefix or (None if provenance == "local" else str(f)),
                states=STATES["command"], tokens_est=estimate_tokens(_read(f)),
            )
        )
    return out


def _scan_agents(base: Path, provenance: str, source_prefix: str | None,
                 repo_root: Path) -> list[Component]:
    out = []
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*.md")):
        name = f.stem
        try:
            rel = str(f.relative_to(repo_root))
        except ValueError:
            rel = str(f)
        out.append(
            Component(
                id=f"agent:{name}", type="subagent", name=name, context="static",
                provenance=provenance,
                path=rel if provenance == "local" else None,
                source=source_prefix or (None if provenance == "local" else str(f)),
                states=STATES["subagent"], tokens_est=estimate_tokens(_read(f)),
            )
        )
    return out


def _matcher_covers_bash(matcher: str | None) -> bool:
    if matcher is None or matcher == "" or matcher == "*":
        return True
    return bool(re.search(r"\bBash\b|\bgit\b", matcher, re.IGNORECASE))


def _scan_hooks_from_settings(settings_path: Path, provenance: str,
                              source_label: str) -> list[Component]:
    out: list[Component] = []
    if not settings_path.is_file():
        return out
    try:
        data = json.loads(_read(settings_path)) or {}
    except json.JSONDecodeError:
        return out
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return out
    seen_names: dict[str, int] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            commands = [
                h.get("command")
                for h in (group.get("hooks") or [])
                if isinstance(h, dict) and isinstance(h.get("command"), str) and h.get("command")
            ]
            matcher_slug = _slug(str(matcher)) if matcher else "all"
            # identity derives from content, never from list position: an insertion above
            # an existing group would shift every index and silently re-key archived usage
            # rows onto the wrong hook (db.py: those rows cannot be re-derived once the
            # transcripts expire). Editing a hook re-keys it instead — the old entry goes
            # `missing` at --refresh, a visible break rather than a silent transplant.
            digest = hashlib.sha1(
                json.dumps([matcher, commands], ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:8]
            name = f"{event}-{matcher_slug}-{digest}"
            dup = seen_names.get(name, 0)
            seen_names[name] = dup + 1
            if dup:
                name = f"{name}-{dup + 1}"  # identical duplicate groups: deterministic tiebreak
            # miss_when auto-inference tentative (§8): PreToolUse and matcher covers Bash/git → git_commit
            miss_when = (
                "git_commit"
                if event == "PreToolUse" and _matcher_covers_bash(matcher)
                else None
            )
            out.append(
                Component(
                    id=f"hook:{name}", type="hook", name=name, context="static",
                    provenance=provenance,
                    source=f"{source_label}#hooks.{event}",
                    states=STATES["hook"], tokens_est=0, miss_when=miss_when,
                    hook_event=str(event), hook_matcher=matcher,
                    hook_commands=commands,
                )
            )
    return out


def _scan_mcp(repo_root: Path) -> list[Component]:
    out = []
    mcp_path = repo_root / ".mcp.json"
    if not mcp_path.is_file():
        return out
    try:
        data = json.loads(_read(mcp_path)) or {}
    except json.JSONDecodeError:
        return out
    servers = data.get("mcpServers") or data.get("servers") or {}
    for name, spec in servers.items() if isinstance(servers, dict) else []:
        # tokens for tool-definition injection into context can't be obtained precisely offline; estimate a lower bound from the server config text
        out.append(
            Component(
                id=f"mcp:{name}", type="mcp", name=name, context="static",
                provenance="local", source=f".mcp.json#{name}",
                states=STATES["mcp"],
                tokens_est=estimate_tokens(json.dumps(spec, ensure_ascii=False)),
            )
        )
    return out


def _scan_claude_md(repo_root: Path) -> list[Component]:
    out = []
    root_md = repo_root / "CLAUDE.md"
    if root_md.is_file():
        out.append(
            Component(
                id="claude-md:root", type="claude_md", name="root", context="static",
                provenance="local", path="CLAUDE.md", states=STATES["claude_md"],
                tokens_est=estimate_tokens(_read(root_md)),
            )
        )
    global_md = Path.home() / ".claude" / "CLAUDE.md"
    if global_md.is_file():
        out.append(
            Component(
                id="claude-md:global", type="claude_md", name="global", context="static",
                provenance="global", source=str(global_md), states=STATES["claude_md"],
                tokens_est=estimate_tokens(_read(global_md)),
            )
        )
    # subdirectory CLAUDE.md: dynamic, has an observable L state
    for f in sorted(repo_root.rglob("CLAUDE.md")):
        rel = f.relative_to(repo_root)
        if str(rel) == "CLAUDE.md":
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        slug = _slug(str(rel.parent))
        out.append(
            Component(
                id=f"claude-md:{slug}", type="claude_md", name=slug, context="dynamic",
                provenance="local", path=str(rel), states=STATES["claude_md"],
                tokens_est=estimate_tokens(_read(f)),
            )
        )
    return out


def _enabled_plugins() -> list[tuple[str, Path]]:
    """(plugin_name, install_path); include only those with settings enabledPlugins == true (spike #7)."""
    home = Path.home()
    settings = home / ".claude" / "settings.json"
    installed = home / ".claude" / "plugins" / "installed_plugins.json"
    enabled: dict[str, bool] = {}
    try:
        enabled = json.loads(_read(settings)).get("enabledPlugins") or {}
    except (json.JSONDecodeError, AttributeError):
        pass
    try:
        plugins = json.loads(_read(installed)).get("plugins") or {}
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for full_name, entries in plugins.items():
        if not enabled.get(full_name):
            continue
        if not isinstance(entries, list) or not entries:
            continue
        install_path = entries[0].get("installPath")
        if install_path and Path(install_path).is_dir():
            short = full_name.split("@", 1)[0]
            out.append((short, Path(install_path)))
    return out


def _scan_plugins(repo_root: Path) -> list[Component]:
    """plugin components keep their native type; provenance=plugin, source=plugin:<name> (A6.1)."""
    out: list[Component] = []
    for name, root in _enabled_plugins():
        src = f"plugin:{name}"
        out.extend(_scan_skills(root / "skills", "plugin", src, repo_root))
        out.extend(_scan_commands(root / "commands", "plugin", src, repo_root))
        out.extend(_scan_agents(root / "agents", "plugin", src, repo_root))
        # plugin hooks (hooks/hooks.json form, included if present)
        hooks_json = root / "hooks" / "hooks.json"
        if hooks_json.is_file():
            comps = _scan_hooks_from_settings(hooks_json, "plugin", src)
            out.extend(comps)
        mcp_json = root / ".mcp.json"
        if mcp_json.is_file():
            try:
                servers = (json.loads(_read(mcp_json)) or {}).get("mcpServers") or {}
                for sname, spec in servers.items():
                    out.append(
                        Component(
                            id=f"mcp:{sname}", type="mcp", name=sname, context="static",
                            provenance="plugin", source=src, states=STATES["mcp"],
                            tokens_est=estimate_tokens(json.dumps(spec, ensure_ascii=False)),
                        )
                    )
            except json.JSONDecodeError:
                pass
    return out


def _dedupe(components: list[Component]) -> list[Component]:
    """On id collision, suffix with provenance to disambiguate (e.g. a local and a plugin skill
    with the same name); a third same-provenance collision (two plugins shipping the same
    command) gets a counter, so ids stay unique and usage rows never conflate."""
    seen: dict[str, Component] = {}
    out = []
    for c in components:
        if c.id in seen:
            cand = f"{c.id}@{c.provenance}"
            n = 2
            while cand in seen:
                cand = f"{c.id}@{c.provenance}-{n}"
                n += 1
            c.id = cand
        seen[c.id] = c
        out.append(c)
    return out


def scan_components(repo_root: Path) -> list[Component]:
    comps: list[Component] = []
    comps.extend(_scan_claude_md(repo_root))
    comps.extend(_scan_skills(repo_root / ".claude" / "skills", "local", None, repo_root))
    comps.extend(_scan_skills(Path.home() / ".claude" / "skills", "global", None, repo_root))
    comps.extend(_scan_commands(repo_root / ".claude" / "commands", "local", None, repo_root))
    comps.extend(_scan_commands(Path.home() / ".claude" / "commands", "global", None, repo_root))
    # subagent: local here, plugin agents via _scan_plugins below; global ~/.claude/agents
    # deliberately excluded (decision #3: the product serves only the current project)
    comps.extend(_scan_agents(repo_root / ".claude" / "agents", "local", None, repo_root))
    comps.extend(
        _scan_hooks_from_settings(repo_root / ".claude" / "settings.json", "local",
                                  ".claude/settings.json")
    )
    comps.extend(
        _scan_hooks_from_settings(Path.home() / ".claude" / "settings.json", "global",
                                  "~/.claude/settings.json")
    )
    comps.extend(_scan_mcp(repo_root))
    comps.extend(_scan_plugins(repo_root))
    return _dedupe(comps)


# ---------------- manifest I/O ----------------

def manifest_path(repo_root: Path) -> Path:
    return repo_root / ".context-render" / "manifest.yaml"


def load_manifest(repo_root: Path) -> list[Component]:
    path = manifest_path(repo_root)
    if not path.is_file():
        raise PreconditionError(
            f"manifest not found ({path}); run ctxr init first"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    comps = [Component.from_yaml_dict(d) for d in data.get("components") or []]
    return comps


def write_manifest(repo_root: Path, components: list[Component]) -> Path:
    path = manifest_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "components": [c.to_yaml_dict() for c in components]}
    path.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return path


def merge_refresh(old: list[Component], new: list[Component]) -> list[Component]:
    """--refresh incremental merge (A3.2): append new components, mark disappeared as missing, keep user-edited fields, recompute tokens."""
    def key(c: Component) -> tuple:
        # name must be part of the key: components without their own path (plugin members,
        # mcp servers) share one source, and a coarser key collapses them into each other
        return (c.type, c.path or c.source or "", c.name)

    new_by_key = {key(c): c for c in new}
    old_keys = {key(o) for o in old}
    merged: list[Component] = []
    for oc in old:
        nc = new_by_key.get(key(oc))
        if nc is None:
            oc.missing = True
            merged.append(oc)
            continue
        # preserve user edits: id, notes, context override, miss_when; recompute tokens and states
        nc.id = oc.id
        nc.notes = oc.notes
        nc.context = oc.context
        if oc.miss_when is not None:
            nc.miss_when = oc.miss_when
        nc.missing = False
        merged.append(nc)
    for c in new:
        if key(c) not in old_keys:
            merged.append(c)
    return merged
