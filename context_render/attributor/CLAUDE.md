# attributor/

Maps parsed events to manifest components as three-state (R/L/I) results with exact/heuristic confidence — most rules are locked to shapes observed in real transcripts, so changing one needs fresh transcript evidence.

| name | topic | when to load |
|---|---|---|
| `__init__.py` | Public surface: `attribute`, `Attribution`, `TimelineEntry`, `UsageAgg` | Re-exporting new attributor API |
| `rules.py` | Three-state rules: state is monotonic per session; evidence capped (50 items / 200-char summaries); `_tag()` is the single sanitation choke point for evidence text; never promote heuristic → exact without transcript evidence | Changing what counts as L/I, evidence content, or timeline entries |
| `bash_heuristics.py` | Bash-mediated file-read / git-commit heuristics; shared shell tokenization (`split_segments*`, memoized) and the one git-subcommand reader (`git_subcmd`, also used by `facts.py` and `bash_mutations.py`); zero false positives beats coverage (AC2a); known false negatives (`<`, `find -exec`, `xargs`, `sed`/`awk`, interpreter reads) are deliberate; unparseable → empty | Changing shell command parsing or heuristic coverage |
| `facts.py` | Self-derivation fact extraction (search / mapping / chain_read) for the SELF-DERIVATION blocks (`report` window scope, session report); `fact_rows` owns the `facts`-table row shape for ingest and live report alike; decontamination rules are locked against real dry-run samples (`grep -v`, filter greps, `find -path`/`-prune`/`-not` never produce keywords); occupancy per context window; `FACTS_EXTRACTOR_VERSION` bumps on any rule change | Changing self-derivation detection, canonical normalization, or occupancy |
| `structure_tokens.py` | Three-layer syntax-token classification (declaration-prefix strip → pure-structure glob → stoplist) feeding the `code structure` action row; prefix/stoplist contents are locked against the corpus | Changing classification rules or the prefix/stoplist sets |
| `stale.py` | Stale-copy window extraction (read → mutated → re-read/compacted/never-re-read) per context window; `STALE_EXTRACTOR_VERSION` bumps on any rule change | Changing stale detection or window semantics |
| `bash_mutations.py` | Bash read/mutation heuristics for the stale gauge (cat/head/tail/sed -n reads; redirect/sed -i/tee/mv/rm targeted writes; git wildcard); zero false positives beats coverage (AC2a) | Changing which bash commands count as stale reads/mutations |
