# attributor/

Maps parsed events to manifest components as three-state (R/L/I) results with exact/heuristic confidence — most behavior implements a frozen SPIKES.md verdict, so read SPIKES.md before changing any rule.

| name | topic | when to load |
|---|---|---|
| `__init__.py` | Public surface: `attribute`, `Attribution`, `TimelineEntry`, `UsageAgg` | Re-exporting new attributor API |
| `rules.py` | Three-state rules: state is monotonic per session; evidence capped (50 items / 200-char summaries); `_tag()` is the single sanitation choke point for evidence text; never promote heuristic → exact without transcript evidence | Changing what counts as L/I, evidence content, or timeline entries |
| `bash_heuristics.py` | Bash-mediated file-read / git-commit heuristics; zero false positives beats coverage (AC2a); known false negatives (`<`, `find -exec`, `xargs`, `sed`/`awk`, interpreter reads) are deliberate; unparseable → empty | Changing shell command parsing or heuristic coverage |
