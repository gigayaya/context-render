# Configuration

## Directory layout

```
<repo>/.context-render/
  manifest.yaml   # produced by init, hand-editable, version-controlled (this is the asset)
  config.yaml     # optional
  db.sqlite       # attribution data (gitignored; an archive — see below, back it up)
  reports/        # --md output (gitignored)
```

### db.sqlite is an archive, not a cache

Claude Code expires transcripts on a rolling window (`cleanupPeriodDays`, default 30 days), so
`~/.claude/projects/**/*.jsonl` is a buffer, not a source of truth. `sync --force` re-parses
everything in well under a second — but it can only ever recover sessions whose transcripts are
still on disk. For anything older than the retention window, the rows in `db.sqlite` are the only
surviving record, and it is gitignored, so nothing else backs it up.

Practical consequences:

- Back up `.context-render/db.sqlite` if you care about history beyond ~30 days.
- Never "delete db.sqlite and rebuild" to fix corruption or a schema mismatch — copy it aside and
  salvage or migrate it. Cheap re-parsing is not the same as reproducible data.
- Raise `cleanupPeriodDays` in `~/.claude/settings.json` to widen the window you can recover from.

`clear` removes all recorded data (db.sqlite and reports/); manifest/config are kept. It lists any
sessions `sync` would not be able to rebuild before asking for confirmation.

## config.yaml (optional, everything has a default)

```yaml
billing: api            # api | subscription | auto (default subscription; subscription hides dollar amounts)
low_use_max_count: 2    # low-use threshold
deadweight_min_sessions: 20
deadweight_min_window_days: 90
in_progress_minutes: 5  # cutoff for last to exclude in-progress sessions
timeline_term_max: 40   # terminal timeline truncation length (--full / --md always complete)
context_window_tokens: 200000  # occupancy-bar denominator (context window map); set 1000000 for 1M-window models ([1m]) — transcripts don't record the window size, so this is the only way to declare it. A peak above the configured value snaps the denominator up to the next known tier (200k → 1M) automatically.
prices:                 # override/extend the built-in price table (USD per MTok; keys prefix-match model)
  claude-example-model: {input: 5.0, output: 25.0}
```

## SessionEnd hook

`init` can optionally install a SessionEnd hook (written to the project's `.claude/settings.json`) that automatically runs `context-render sync --since 1d` for a lightweight ingest when a session ends; installation is idempotent. Sessions that terminate abnormally are caught up incrementally by a manual `sync`.

**To remove**: run `context-render remove-hook` (deletes only the entry under `hooks.SessionEnd` whose command is `context-render sync --since 1d`; everything else in `settings.json` is left untouched).
