# attributor/

Maps parsed events to manifest components as three-state (R/L/I) results with exact/heuristic confidence — most behavior implements a frozen SPIKES.md verdict, so read SPIKES.md before changing any rule.

| name | topic | when to load |
|---|---|---|
| `__init__.py` | Public surface: `attribute`, `Attribution`, `TimelineEntry`, `UsageAgg` | Re-exporting new attributor API |
| `rules.py` | Three-state rules: state is monotonic per session; evidence capped (50 items / 200-char summaries); `_tag()` is the single sanitation choke point for evidence text; never promote heuristic → exact without transcript evidence | Changing what counts as L/I, evidence content, or timeline entries |
| `bash_heuristics.py` | Bash-mediated file-read / git-commit heuristics; zero false positives beats coverage (AC2a); known false negatives (`<`, `find -exec`, `xargs`, `sed`/`awk`, interpreter reads) are deliberate; unparseable → empty | Changing shell command parsing or heuristic coverage |
| `facts.py` | Self-derivation fact extraction (search / mapping / chain_read) for `analyze`; decontamination rules are frozen W3 verdicts (`grep -v`, filter greps, `find -path`/`-prune`/`-not` never produce keywords); occupancy per context window; `FACTS_EXTRACTOR_VERSION` bumps on any rule change | Changing analyze detection, canonical normalization, or occupancy |
| `structure_tokens.py` | Three-layer syntax-token classification (declaration-prefix strip → pure-structure glob → stoplist) feeding the `code structure` action row; prefix/stoplist contents are frozen W4 verdicts | Changing classification rules or the prefix/stoplist sets |
| `stale.py` | Stale-copy window extraction (read → mutated → re-read/compacted/never-re-read) per context window; `STALE_EXTRACTOR_VERSION` bumps on any rule change; design frozen in specs/2026-08-01-stale-gauge-design.md | Changing stale detection or window semantics |
| `bash_mutations.py` | Bash read/mutation heuristics for the stale gauge (cat/head/tail/sed -n reads; redirect/sed -i/tee/mv/rm targeted writes; git wildcard); zero false positives beats coverage (AC2a) | Changing which bash commands count as stale reads/mutations |
