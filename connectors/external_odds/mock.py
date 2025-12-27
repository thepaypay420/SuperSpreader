from __future__ import annotations

import random

from connectors.external_odds.base import ExternalOdds, ExternalOddsProvider
from trading.types import MarketInfo
from utils.pricing import clamp


class MockOddsProvider(ExternalOddsProvider):
    """
    Mock external odds:
    - Uses a deterministic-ish pseudo "fair prob" based on market_id hash,
      plus small noise so strategies exercise both sides.
    """

    def __init__(self, noise: float = 0.02, seed: int = 7):
        self.noise = float(noise)
        self._rng = random.Random(seed)

    async def get_fair_prob(self, market: MarketInfo) -> ExternalOdds:
        base = (abs(hash(market.market_id)) % 1000) / 1000.0
        base = 0.2 + 0.6 * base  # keep away from extremes
        jitter = self._rng.uniform(-self.noise, self.noise)
        fair = clamp(base + jitter, 0.01, 0.99)
        return ExternalOdds(fair_prob=fair, source="mock")

