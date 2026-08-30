"""SessionEnd hook install/remove (A10; spike #9 main case = SessionEnd).

- Writes to the project's .claude/settings.json; MUST be idempotent (repeated init does not re-insert).
- Abnormally terminated sessions are backfilled incrementally by sync.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import PreconditionError

HOOK_COMMAND = "ctxr sync --since 1d"
# hooks written before the executable was renamed (≤0.5.0); still matched so
# remove-hook cleans them up and install upgrades them instead of duplicating
LEGACY_HOOK_COMMANDS = ("context-render sync --since 1d",)
HOOK_EVENT = "SessionEnd"


def _matches(command: object, needles: tuple[str, ...]) -> bool:
    return any(n in str(command) for n in needles)


def _settings_path(repo_root: Path) -> Path:
    return repo_root / ".claude" / "settings.json"


def is_installed(repo_root: Path) -> bool:
    path = _settings_path(repo_root)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    hooks = data.get("hooks")
    groups = hooks.get(HOOK_EVENT) if isinstance(hooks, dict) else None
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks", []) or []:
            if isinstance(h, dict) and _matches(h.get("command", ""), (HOOK_COMMAND, *LEGACY_HOOK_COMMANDS)):
                return True
    return False


def install(repo_root: Path) -> bool:
    """Returns True = installed (or upgraded a legacy command) this call; False = already current."""
    path = _settings_path(repo_root)
    data: dict = {}
    if path.is_file():
        # never overwrite settings we could not fully read back (a rewrite would drop them)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError as e:
            raise PreconditionError(
                f"{path} is not valid JSON ({e}); fix it manually before installing the hook"
            )
        if not isinstance(data, dict):
            raise PreconditionError(
                f"{path} top level is not a JSON object; fix it manually before installing the hook"
            )
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise PreconditionError(
            f'{path} "hooks" is not a JSON object; fix it manually before installing the hook'
        )
    groups = hooks.setdefault(HOOK_EVENT, [])
    if not isinstance(groups, list):
        raise PreconditionError(
            f'{path} "hooks.{HOOK_EVENT}" is not a JSON array; fix it manually before installing the hook'
        )
    upgraded = False
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for h in group["hooks"]:
            if not isinstance(h, dict):
                continue
            if _matches(h.get("command", ""), (HOOK_COMMAND,)):
                return False  # already current (idempotent no-op)
            if _matches(h.get("command", ""), LEGACY_HOOK_COMMANDS):
                h["command"] = HOOK_COMMAND
                upgraded = True
    if not upgraded:
        groups.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def uninstall(repo_root: Path) -> bool:
    path = _settings_path(repo_root)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    hooks = data.get("hooks")
    groups = hooks.get(HOOK_EVENT) if isinstance(hooks, dict) else None
    if not isinstance(groups, list) or not groups:
        return False
    new_groups = []
    removed = False
    for group in groups:
        # unknown shapes pass through untouched — never destroy settings we don't understand
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            new_groups.append(group)
            continue
        kept = [h for h in group["hooks"]
                if not (isinstance(h, dict)
                        and _matches(h.get("command", ""), (HOOK_COMMAND, *LEGACY_HOOK_COMMANDS)))]
        if len(kept) != len(group["hooks"]):
            removed = True
        if kept:
            group["hooks"] = kept
            new_groups.append(group)
    if not removed:
        return False  # nothing matched: do not rewrite (reformat) the file
    if new_groups:
        data["hooks"][HOOK_EVENT] = new_groups
    else:
        data["hooks"].pop(HOOK_EVENT, None)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return removed
