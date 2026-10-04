# Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check context_render tests
```

## Releasing

Releases go to PyPI from `.github/workflows/release.yml` via Trusted Publishing (no API token stored anywhere).

1. Bump `__version__` in `context_render/__init__.py` — `pyproject.toml` reads it from there.
2. Commit, then tag with the same version: `git tag v1.0.0`.
3. Push the tag: `git push origin v1.0.0`. The workflow runs lint + tests, refuses a tag that doesn't match `__version__`, builds, runs `twine check`, and publishes.

A version number can be uploaded to PyPI only once — even a deleted release can't reuse it. To rehearse on TestPyPI first, run the workflow by hand (`gh workflow run release.yml --ref main`, or "Run workflow" in the Actions tab): it builds the same way but skips the tag check and publishes to TestPyPI instead. TestPyPI also accepts each version once, so a second rehearsal needs a bumped `__version__`. Install the result with:

```bash
pipx install --index-url https://test.pypi.org/simple/ --pip-args="--extra-index-url https://pypi.org/simple/" context-render
```

To check a build locally first:

```bash
pip install build twine
python -m build && twine check --strict dist/*
```

One-time setup: on pypi.org, Publishing → add a pending publisher for `gigayaya/context-render`, workflow `release.yml`, environment `pypi`. Do the same on test.pypi.org with environment `testpypi` — the two sites keep separate accounts and publishers.
