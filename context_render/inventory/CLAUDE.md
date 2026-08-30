# inventory/

Scans the repo's scaffolding into `.context-render/manifest.yaml` — the manifest is a hand-edited, version-controlled asset, so refresh must merge user edits, never clobber them.

| name | topic | when to load |
|---|---|---|
| `__init__.py` | Public surface: `Component`, `scan_components`, `load_manifest`, `write_manifest`, `merge_refresh`, `estimate_tokens` | Re-exporting new inventory API |
| `scanner.py` | Component scan + manifest I/O; `merge_refresh` preserves user edits (`id`/`notes`/`context`/`miss_when`) and marks disappeared components `missing`; `STATES` is the R/L/I applicability matrix (changing it is a design change); skill static tokens count metadata only; the manifest stores `name` explicitly (attribution matches by name — a dedupe-suffixed id must never leak into it) | Adding a component type/source or changing manifest fields |
| `tokens.py` | Token estimation `ceil(utf8_len/4)`, always labeled an estimate (CJK overestimates); no tokenizer dependency — zero-API is the constraint | Changing token estimation |
