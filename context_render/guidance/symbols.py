"""Python symbol enumeration (guidance-reachability spec §4).

v1 is Python-only by verdict: reference extraction and the closure are language-neutral;
only this counting layer is language-bound. No abstraction layer is pre-built for other
languages — that waits for a real need (house rule).
"""

from __future__ import annotations

import ast
from pathlib import Path

_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def py_symbols(path: Path) -> list[str] | None:
    """def/class names in source order (nested included). None on unreadable or
    syntactically invalid source — callers count parse-failed files separately so the
    denominator stays honest (parse-failed ≠ zero symbols)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return None
    out: list[str] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF_NODES):
                out.append(child.name)
            visit(child)

    visit(tree)
    return out
