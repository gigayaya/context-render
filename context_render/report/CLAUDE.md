# report/

Pure-function renderers over the intermediate aggregate object — terminal and markdown share the same line builders so their plain output is byte-identical (AC3/AC9), and all drawing is pure stdlib characters (AC6).

| name | topic | when to load |
|---|---|---|
| `__init__.py` | Empty package marker | — |
| `aggregate.py` | Builds the intermediate aggregate object (`schema_version`, built-in hints, deadweight verdicts); renderers never touch store/filesystem | Changing what data a report contains |
| `ansi.py` | Opt-in ANSI `Style`; honors NO_COLOR / non-tty / CLICOLOR_FORCE; color applied AFTER padding; md renderer never passes a style | Changing colors or style detection |
| `charts.py` | CJK-safe display width (`display_width`/`pad_to`/`truncate_display` — never `len()`), bar/histogram drawing | Any alignment/width or chart-drawing work |
| `context_map.py` | Context-window map; worked ASCII example in the module docstring; the two bars share one column mapping and cross-reference timeline row numbers | Changing the map's geometry or labels |
| `render_md.py` | Markdown renderer; reuses `render_term` line builders; `_fence()` sizes fences longer than any backtick run | Changing markdown file output |
| `render_term.py` | Terminal renderer; `session_lines`/`window_lines` shared with md; fixed `WRAP_WIDTH = 96` (not live terminal width) | Adding or altering report lines/sections |
| `timeline.py` | Timeline assembly and row rendering; action-tag colors (read=blue, edit/write=yellow, bash=magenta) | Changing timeline rows or format |
