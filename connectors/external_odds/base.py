from __future__ import annotations

import abc
from dataclasses import dataclass

from trading.types import MarketInfo


@dataclass(frozen=True)
class ExternalOdds:
    fair_prob: float
    source: str


class ExternalOddsProvider(abc.ABC):
    @abc.abstractmethod
    async def get_fair_prob(self, market: MarketInfo) -> ExternalOdds:
        """
        Return external reference fair probability for this market outcome.
        """
        raise NotImplementedError

