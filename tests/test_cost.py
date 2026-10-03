"""cost engine tests: usage summation, apportioning formula, no-usage degrade, unknown model."""

import pytest

from context_render.config import Config
from context_render.cost import compute_session_cost
from context_render.parser.loader import Event, Usage


def _ev(model="claude-opus-4-8", **usage_kw):
    return Event(idx=0, kind="assistant_msg", raw_type="assistant",
                 model=model, usage=Usage(**usage_kw))


def test_usage_sum_and_price():
    # opus 4.x: $5 in / $25 out; cache write 1.25x, cache read 0.1x
    ev = _ev(input_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
             cache_read_input_tokens=1_000_000, output_tokens=1_000_000)
    d = compute_session_cost([ev], static_tokens_s=0, config=Config())
    # 5 + 5*1.25 + 5*0.1 + 25 = 36.75
    assert d.cost_usd == pytest.approx(36.75)
    assert d.has_usage


@pytest.mark.parametrize(
    "model,price",
    [
        # Opus 4.0's id has no minor-version segment, so only the bare `claude-opus-4`
        # prefix can reach it — that prefix therefore has to carry the old 15/75 tier,
        # and every cheaper release overrides it with a longer key.
        ("claude-opus-4-20250514", {"input": 15.0, "output": 75.0}),
        ("claude-opus-4-1-20250805", {"input": 15.0, "output": 75.0}),
        ("claude-opus-4-5-20251101", {"input": 5.0, "output": 25.0}),
        ("claude-opus-4-8", {"input": 5.0, "output": 25.0}),
        ("claude-sonnet-5", {"input": 3.0, "output": 15.0}),
        ("claude-haiku-4-5", {"input": 1.0, "output": 5.0}),
        ("claude-fable-5", {"input": 10.0, "output": 50.0}),
        ("some-other-model", None),  # unmatched → unknown_models warning, never a silent price
    ],
)
def test_price_prefix_tiers(model, price):
    assert Config().price_for(model) == price


def test_static_fold_formula():
    # r_t = S/C_t; C_t = in + cache_read + cache_creation
    ev = _ev(input_tokens=2000, cache_creation_input_tokens=0,
             cache_read_input_tokens=8000, output_tokens=0)
    d = compute_session_cost([ev], static_tokens_s=1000, config=Config())
    # C_t=10000, r_t=0.1; cost_t = 2000*5e-6 + 8000*5e-6*0.1 = 0.01+0.004=0.014
    assert d.cost_usd == pytest.approx(0.014)
    assert d.static_cost_usd == pytest.approx(0.0014)


def test_static_fold_capped_at_1():
    ev = _ev(input_tokens=100, output_tokens=0)
    d = compute_session_cost([ev], static_tokens_s=10_000, config=Config())
    assert d.static_cost_usd == pytest.approx(d.cost_usd)  # r_t capped at 1.0


def test_no_usage_degrades():
    ev = Event(idx=0, kind="assistant_msg", raw_type="assistant")
    d = compute_session_cost([ev], static_tokens_s=0, config=Config())
    assert d.cost_usd is None and not d.has_usage


def test_unknown_model():
    ev = _ev(model="claude-hyperion-9", input_tokens=1000)
    d = compute_session_cost([ev], static_tokens_s=0, config=Config())
    assert d.cost_usd is None
    assert d.unknown_models == ["claude-hyperion-9"]


def test_price_override():
    cfg = Config(prices={"claude-hyperion-9": {"input": 1.0, "output": 2.0}})
    ev = _ev(model="claude-hyperion-9", input_tokens=1_000_000)
    d = compute_session_cost([ev], static_tokens_s=0, config=cfg)
    assert d.cost_usd == pytest.approx(1.0)
