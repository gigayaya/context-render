"""`.context-render/config.yaml` loading and defaults (summary of the plan's §8 open list)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import PreconditionError

AUDIT_DIR = ".context-render"

# Built-in price table (USD per MTok; cache write=1.25x input, cache read=0.1x input).
# prices in config.yaml can override; keys are prefix-matched against message.model,
# longest prefix wins.
#
# The Opus 4 keys must stay enumerated: the family spans two price tiers (4.0/4.1 at 15/75,
# 4.5 onward at 5/25), and Opus 4.0's id is `claude-opus-4-<date>` — it has no minor-version
# segment to key on, so it can only be reached by the bare `claude-opus-4` prefix. That prefix
# therefore carries the old price and every cheaper release overrides it with a longer key.
# A future Opus 4.x lands on the old tier until it is added here, which overstates cost rather
# than understating it silently.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "claude-fable-5": {"input": 10.0, "output": 50.0},
    "claude-mythos-5": {"input": 10.0, "output": 50.0},
    "claude-opus-4": {"input": 15.0, "output": 75.0},
    "claude-opus-4-1": {"input": 15.0, "output": 75.0},
    "claude-opus-4-5": {"input": 5.0, "output": 25.0},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


@dataclass
class Config:
    low_use_max_count: int = 2
    billing: str = "subscription"  # api | subscription | auto
    deadweight_min_sessions: int = 20
    deadweight_min_window_days: int = 90
    in_progress_minutes: int = 5
    timeline_term_max: int = 40
    graph: bool = True
    timeline: bool = True
    context_window_tokens: int = 200_000  # occupancy-bar denominator (context window map)
    prices: dict = field(default_factory=dict)

    def price_for(self, model: str | None) -> dict[str, float] | None:
        if not model:
            return None
        merged = {**DEFAULT_PRICES, **{k: v for k, v in self.prices.items()}}
        # longest prefix wins
        best = None
        for prefix, p in merged.items():
            if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, p)
        return best[1] if best else None


def audit_dir(repo_root: Path) -> Path:
    return repo_root / AUDIT_DIR


_INT_KEYS = (
    "low_use_max_count",
    "deadweight_min_sessions",
    "deadweight_min_window_days",
    "in_progress_minutes",
    "timeline_term_max",
    "context_window_tokens",
)
_BOOL_KEYS = ("graph", "timeline")


def _validate(cfg: Config) -> None:
    """Bad values must fail here as exit-3 precondition errors, not as tracebacks
    deep inside a later comparison or render (errors.py contract)."""
    for key in _INT_KEYS:
        v = getattr(cfg, key)
        if not isinstance(v, int) or isinstance(v, bool):
            raise PreconditionError(f"config.{key} must be an integer, got {v!r}")
    for key in _BOOL_KEYS:
        v = getattr(cfg, key)
        if not isinstance(v, bool):
            raise PreconditionError(f"config.{key} must be true/false, got {v!r}")
    if cfg.billing not in ("api", "subscription", "auto"):
        raise PreconditionError(
            f"config.billing invalid value: {cfg.billing} (must be api|subscription|auto)")
    if not isinstance(cfg.prices, dict):
        raise PreconditionError(f"config.prices must be a mapping, got {cfg.prices!r}")
    for prefix, price in cfg.prices.items():
        ok = isinstance(price, dict) and all(
            isinstance(price.get(k), (int, float)) and not isinstance(price.get(k), bool)
            for k in ("input", "output")
        )
        if not ok:
            raise PreconditionError(
                f"config.prices[{prefix!r}] must be a mapping with numeric input/output")


def load_config(repo_root: Path) -> Config:
    path = audit_dir(repo_root) / "config.yaml"
    if not path.exists():
        return Config()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise PreconditionError(f"config.yaml invalid YAML: {e}")
    if not isinstance(data, dict):
        raise PreconditionError("config.yaml top level must be a mapping")
    cfg = Config()
    for key in (*_INT_KEYS, *_BOOL_KEYS, "billing", "prices"):
        if key in data:
            setattr(cfg, key, data[key])
    _validate(cfg)
    return cfg


def find_repo_root(start: Path | None = None) -> Path:
    """Search upward from cwd for .context-render or .git; if neither found, return cwd."""
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / AUDIT_DIR).is_dir() or (p / ".git").exists():
            return p
    return cur
