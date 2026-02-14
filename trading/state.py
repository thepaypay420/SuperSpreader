from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trading.types import MarketInfo, TopOfBook, TradeTick

if TYPE_CHECKING:
    from trading.candles import CandleAggregator


@dataclass
class SharedState:
    """
    Async-shared state for the running system.
    """

    markets: dict[str, MarketInfo] = field(default_factory=dict)
    ranked_markets: list[str] = field(default_factory=list)  # market_ids in rank order

    tob: dict[str, TopOfBook] = field(default_factory=dict)
    last_trade: dict[str, TradeTick] = field(default_factory=dict)

    last_book_update_ts: float = field(default_factory=lambda: 0.0)
    last_trade_update_ts: float = field(default_factory=lambda: 0.0)

    # 5-minute candle aggregator (shared across feed + strategy loops)
    candle_aggregator: CandleAggregator | None = field(default=None)

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def now(self) -> float:
        return time.time()

