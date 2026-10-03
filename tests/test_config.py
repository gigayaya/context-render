"""config.yaml values are validated at load: bad types exit as PreconditionError (exit 3),
never as a traceback deep inside a later render (errors.py contract)."""

import pytest

from context_render.config import load_config
from context_render.errors import PreconditionError


def _cfg(tmp_path, text):
    d = tmp_path / ".context-render"
    d.mkdir(exist_ok=True)
    (d / "config.yaml").write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("yaml_text", [
    "low_use_max_count: five",
    "timeline_term_max: [1]",
    "graph: 3",
    "prices: [a, b]",
    "prices: {x: {input: 1}}",  # missing output
    "prices: {x: [1, 2]}",
    "context_window_tokens: '200000'",  # quoted string is not an integer
])
def test_load_config_rejects_bad_types(tmp_path, yaml_text):
    with pytest.raises(PreconditionError):
        load_config(_cfg(tmp_path, yaml_text))


def test_load_config_accepts_valid_overrides(tmp_path):
    cfg = load_config(_cfg(
        tmp_path,
        "low_use_max_count: 0\nprices:\n  my-model:\n    input: 1.5\n    output: 6\n",
    ))
    assert cfg.low_use_max_count == 0
    assert cfg.price_for("my-model-123") == {"input": 1.5, "output": 6}
