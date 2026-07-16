# parser/

All transcript-format knowledge is encapsulated in this package — a Claude Code version bump should only touch code here, and parsing must degrade to a warning (never crash) on unknown input.

| name | topic | when to load |
|---|---|---|
| `__init__.py` | Public surface: `parse_file`, `discover_sessions`, `Event`, `ParsedSession`, `Usage`, `SessionFile` | Re-exporting new parser API |
| `discovery.py` | Session-file discovery; transcript `cwd` is authoritative for session→repo attribution; a `SessionFile` spans main + sidechain files (mtime/size aggregate so a grown subagent file re-triggers ingest) | Changing how sessions are found, matched to a repo, or re-ingested |
| `loader.py` | JSONL → `Event` stream; unknown `type`s degrade; new aux types enter `KNOWN_AUX_TYPES` only after observation in a real transcript | Supporting new transcript line shapes or event kinds |
| `versions.py` | Supported version matrix (`2.1.*`) | Widening version support (needs real-transcript validation + SPIKES.md entry) |
