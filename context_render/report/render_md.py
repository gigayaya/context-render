"""markdown renderer: full report to file + stdout; charts and timeline locked to monospace via code block.

Shares the same line builders as the terminal (render_term.session_lines / window_lines),
guaranteeing the two forms stay consistent.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..config import Config
from .render_term import map_lines, session_lines, window_lines

TITLES = {"last": "context-render — session report", "report": "context-render — aggregate report",
          "map": "context-render — map"}

BODY_FNS = {"last": session_lines, "map": map_lines}


def _fence(body: str) -> str:
    """A fence must be longer than any backtick run in the body, or content escapes the block."""
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", body)), default=0)
    return "`" * max(3, longest + 1)


def render_md(agg: dict, config: Config) -> str:
    body_fn = BODY_FNS.get(agg["report_type"], window_lines)
    body = "\n".join(body_fn(agg, config, full=True))
    title = TITLES.get(agg["report_type"], "context-render")
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    fence = _fence(body)
    return f"# {title}\n\nGenerated: {generated}\n\n{fence}\n{body}\n{fence}\n"


def write_md(agg: dict, config: Config, repo_root: Path) -> Path:
    content = render_md(agg, config)
    out_dir = repo_root / ".context-render" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{agg['report_type']}-{stamp}.md"
    path.write_text(content, encoding="utf-8")
    return path
