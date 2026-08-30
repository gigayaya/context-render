"""Python symbol enumeration (guidance-reachability spec §4): file-level reachability is
the deliberate proxy for symbol findability — routing's job ends at the right file."""

from __future__ import annotations

from context_render.guidance.symbols import py_symbols


def test_defs_classes_and_nesting(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        "class A:\n"
        "    def method(self): ...\n"
        "async def fetch(): ...\n"
        "def outer():\n"
        "    def inner(): ...\n",
        encoding="utf-8")
    assert py_symbols(p) == ["A", "method", "fetch", "outer", "inner"]


def test_empty_file(tmp_path):
    p = tmp_path / "e.py"
    p.write_text("", encoding="utf-8")
    assert py_symbols(p) == []


def test_syntax_error_returns_none(tmp_path):
    # None (not []) so callers can keep the denominator honest: parse-failed ≠ zero symbols
    p = tmp_path / "bad.py"
    p.write_text("def broken(:\n", encoding="utf-8")
    assert py_symbols(p) is None


def test_unreadable_returns_none(tmp_path):
    assert py_symbols(tmp_path / "missing.py") is None
