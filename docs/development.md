# Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check context_render tests
```

Spike decisions are recorded in [`SPIKES.md`](../SPIKES.md).
