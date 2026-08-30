"""Cost engine (A9): measured first, shares primary, amounts secondary.

- session measured: Σ assistant usage × built-in price table (by message.model, config-overridable).
- static apportioning: per-turn r_t = S/C_t ratio approximation; being an approximation, the report MUST mark it "approx.".
- usage unavailable → whole chain degrades to estimate and is marked (spike #8 main case; this is the fallback).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CACHE_READ_MULT, CACHE_WRITE_MULT, Config
from .parser.loader import Event


@dataclass
class CostDetail:
    cost_usd: float | None = None  # None = no usage or no price table (degraded)
    static_cost_usd: float | None = None  # static context spend, apportioned (approx.)
    static_tokens_s: int = 0  # manifest static total S at time of apportioning
    input_like_tokens: int = 0  # Σ (input + cache_read + cache_creation)
    output_tokens: int = 0
    has_usage: bool = False
    unknown_models: list[str] | None = None


def compute_session_cost(events: list[Event], static_tokens_s: int,
                         config: Config) -> CostDetail:
    detail = CostDetail(static_tokens_s=static_tokens_s, unknown_models=[])
    total = 0.0
    static_total = 0.0
    priced_any = False
    for ev in events:
        if ev.kind != "assistant_msg" or ev.usage is None:
            continue
        detail.has_usage = True
        u = ev.usage
        c_t = u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens
        detail.input_like_tokens += c_t
        detail.output_tokens += u.output_tokens
        price = config.price_for(ev.model)
        if price is None:
            if ev.model and ev.model not in detail.unknown_models:
                detail.unknown_models.append(ev.model)
            continue
        p_in = price["input"] / 1_000_000
        cost_t = (
            u.input_tokens * p_in
            + u.cache_creation_input_tokens * p_in * CACHE_WRITE_MULT
            + u.cache_read_input_tokens * p_in * CACHE_READ_MULT
            + u.output_tokens * price["output"] / 1_000_000
        )
        total += cost_t
        priced_any = True
        if c_t > 0 and static_tokens_s > 0:
            r_t = min(1.0, static_tokens_s / c_t)
            static_total += cost_t * r_t
    if priced_any:
        detail.cost_usd = round(total, 6)
        detail.static_cost_usd = round(static_total, 6)
    return detail
