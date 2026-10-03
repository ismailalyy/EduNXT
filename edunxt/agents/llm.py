"""Anthropic client factory and per-case usage/cost tracking."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from importlib import resources

import anthropic

log = logging.getLogger(__name__)

# USD per million tokens (input, output). Estimates for cost logging only.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
WEB_SEARCH_PRICE = 10.00 / 1000  # per search request


def make_client(api_key: str = "") -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def load_prompt(name: str) -> str:
    return resources.files("edunxt.agents.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


@dataclass
class UsageMeter:
    """Accumulates token usage across the (possibly parallel) calls made for one job."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, model: str, usage) -> None:
        if usage is None:
            return
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        stu = getattr(usage, "server_tool_use", None)
        searches = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        in_price, out_price = PRICES.get(model, PRICES["claude-opus-5-5"])
        cost = (
            inp * in_price + out * out_price + cr * in_price * 0.1 + cw * in_price * 1.25
        ) / 1_000_000 + searches * WEB_SEARCH_PRICE
        with self._lock:
            self.input_tokens += inp
            self.output_tokens += out
            self.cache_read_tokens += cr
            self.cache_write_tokens += cw
            self.web_searches += searches
            self.cost_usd += cost

    def describe(self) -> str:
        return (
            f"in={self.input_tokens} out={self.output_tokens} cache_read={self.cache_read_tokens} "
            f"searches={self.web_searches} est_cost=${self.cost_usd:.2f}"
        )
