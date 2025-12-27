from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from connectors.external_odds.base import ExternalOddsProvider
from execution.base import Broker
from risk.portfolio import Portfolio
from risk.rules import RiskEngine
from storage.sqlite import SqliteStore
from trading.state import SharedState


@dataclass(frozen=True)
class StrategyContext:
    settings: Any
    state: SharedState
    store: SqliteStore
    broker: Broker
    risk: RiskEngine
    portfolio: Portfolio
    odds: ExternalOddsProvider


class Strategy(abc.ABC):
    name: str

    @abc.abstractmethod
    async def on_market(self, ctx: StrategyContext, market_id: str) -> None:
        """
        Called periodically for each active market (top-ranked).
        Strategy may place/cancel orders (paper or live).
        """
        raise NotImplementedError

