# store/

SQLite persistence — the DB is an archive, not a cache: transcripts expire (~30 days) so rows may be the only surviving record; never repair by delete-and-rebuild, migrate in place, and `clear` is the only sanctioned deletion path.

| name | topic | when to load |
|---|---|---|
| `__init__.py` | Public surface: `Store`, `SCHEMA_VERSION` | Re-exporting new store API |
| `db.py` | Idempotent writes (session id is PK; BEGIN → DELETE → re-insert → COMMIT); append-only `MIGRATIONS` (never rewrite a shipped step, never drop rows, re-derive from existing rows not transcripts); reserved `_`-prefixed component ids | Changing persistence or adding a schema migration |
| `schema.sql` | DDL; ships as package data (declared in pyproject); must stay executable as one script | Any schema change (bump `SCHEMA_VERSION` + add the migration step in the same commit) |
