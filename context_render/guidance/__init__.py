"""Guidance graph: format-neutral path-reference extraction and static reachability.

Shared foundation for `ctxr map` (static, this package + mapdev/) and the
future acquisition trace (specs/2026-07-19-guidance-trace.md).
"""

from .refs import FileIndex, Ref, StaleRef, extract_refs  # noqa: F401
