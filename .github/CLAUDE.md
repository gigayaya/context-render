# .github/

GitHub configuration — CI commands must stay aligned with pyproject (ruff line-length 100, target py311) and `docs/development.md`; when a step changes, all three change together.

| name | topic | when to load |
|---|---|---|
| `workflows/ci.yml` | Test matrix on Python 3.11/3.12: `pip install -e ".[dev]"` → `ruff check context_render tests` → `pytest -q` | Changing lint/test commands, Python version support, or dependencies |
