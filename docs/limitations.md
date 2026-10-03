# Limitations

Read these before making any delete decisions.

1. **Used ≠ useful**: this tool answers coverage, not effectiveness. The code-coverage analogy holds: 100% coverage doesn't mean correct, 0% coverage is worth suspicion.
2. **Attribution gap for CLAUDE.md prose rules**: root/global CLAUDE.md files are always loaded at the file level, but whether individual rules were followed **cannot** be observed from the event stream (rule-level compliance is deferred to M2). The prescription: move task-specific knowledge into a skill, making it measurable, on-demand dynamic context.
3. **Hook triggering is best-effort**: it depends on how much the transcript records (`hook_success` attachment, `stop_hook_summary`); the event→manifest-entry mapping is heuristic and always annotated with a confidence level.
4. **Correlation ≠ causation**: low use may simply mean no relevant task fell within the window; the delete decision is yours. Before deleting, consider widening the observation window (`--since 90d`).
5. **Bash-mediated access is heuristic**: zero tolerance for false positives takes priority over false negatives. Candidate paths must pass three gates: shell metacharacter filtering (redirects/substitutions/wildcards on improper paths), correct command segmentation (`;`, `|`, `&`, and unquoted newlines, attached or not; quoted ones stay literal), and **the file actually existing**. Known false negatives (not detected): redirect `<`, `find -exec`, `xargs`, `sed`/`awk`, indirect file reads via scripts/interpreters, complex commands with unbalanced quotes, and files deleted after the session. git commit detection requires double confirmation from tool_result.
6. **Subdirectory CLAUDE.md detection depends on how much the transcript records**: currently degraded to a directory-activity heuristic (Read/Edit/Write/Bash activity under that directory → presumed loaded), confidence=heuristic.
7. **The R state can be distorted**: attribution doesn't compare against the settings snapshot as it was during the historical session; when the manifest and historical sessions are out of temporal sync, R (and the MISS determination) may diverge from what was actually true at the time.
8. **Token numbers are always estimates**: `ceil(utf8_len/4)`. Files that are mostly Chinese have a larger error (Chinese characters are 3 bytes ≈ 0.75 tokens/char, so they're overestimated; observed error is commonly +30–80%); M1 doesn't call the count-tokens API (to stay zero-API), M2 offers optional precision.
9. **Cost estimation is approximate**: static context overhead is amortized per turn at the ratio `r_t = S/C_t`; `subscription` accounts show no dollar amounts (marginal cost is zero, so showing it would mislead), only tokens and share.
10. **Compaction observation**: identified from known event shapes (`compact_boundary` / `isCompactSummary`); if your Claude Code version records it in a different shape, the timeline won't show a compaction row.
11. **Version support matrix**: 2.1.x (sampled 2.1.156–2.1.207). Other versions get a warning + best-effort parsing, with all attribution marked heuristic; unknown event types don't crash — they're counted, skipped, and marked `degraded`.
12. **`ctxr map` audits the repo only**: the user-global `~/.claude/CLAUDE.md` is always loaded by Claude Code but is not a start node for reachability or dead-route detection — its references cannot be resolved against the repo tree. Reachability numbers therefore exclude anything only the global file routes to.

## Privacy

Everything stays local: zero uploads, zero telemetry, zero API calls in the core flow (runs offline). Transcripts are read-only; outputs (manifest / db / reports) all live under your repo.
