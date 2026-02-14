"""
5-minute OHLCV candle aggregator.

Converts a stream of TopOfBook / TradeTick updates into time-bucketed candles.
Each candle spans exactly `interval_secs` seconds (default 300 = 5 minutes).

Design:
- Uses mid-price from TOB as the "price" when no trades are available.
- Prefers actual trade prices when they arrive.
- Accumulates volume from trade ticks.
- Emits a completed Candle when the interval boundary is crossed.
- Keeps a configurable history of closed candles per market for indicator computation.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candle:
    """A single OHLCV candle."""
    market_id: str
    open_ts: float          # start of the candle window (epoch)
    close_ts: float         # end of the candle window (epoch)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0     # sum of trade sizes in window
    trade_count: int = 0    # number of trade ticks in window
    vwap: float = 0.0       # volume-weighted average price in this candle
    complete: bool = False   # True once the candle interval has elapsed

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "open_ts": self.open_ts,
            "close_ts": self.close_ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "vwap": self.vwap,
            "complete": self.complete,
        }


@dataclass
class _CandleBuilder:
    """Accumulates ticks for a single in-progress candle."""
    market_id: str
    open_ts: float
    close_ts: float
    open: float = 0.0
    high: float = -math.inf
    low: float = math.inf
    close: float = 0.0
    volume: float = 0.0
    trade_count: int = 0
    _vwap_num: float = 0.0   # sum(price * size)
    _vwap_den: float = 0.0   # sum(size)
    _started: bool = False

    def update_price(self, price: float, volume: float = 0.0, is_trade: bool = False) -> None:
        if not self._started:
            self.open = price
            self._started = True
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        if is_trade and volume > 0:
            self.volume += volume
            self.trade_count += 1
            self._vwap_num += price * volume
            self._vwap_den += volume

    def to_candle(self, complete: bool = False) -> Candle:
        vwap = (self._vwap_num / self._vwap_den) if self._vwap_den > 0 else self.close
        return Candle(
            market_id=self.market_id,
            open_ts=self.open_ts,
            close_ts=self.close_ts,
            open=self.open,
            high=self.high if self.high > -math.inf else self.close,
            low=self.low if self.low < math.inf else self.close,
            close=self.close,
            volume=self.volume,
            trade_count=self.trade_count,
            vwap=vwap,
            complete=complete,
        )


class CandleAggregator:
    """
    Aggregates price updates into fixed-interval candles for multiple markets.

    Usage:
        agg = CandleAggregator(interval_secs=300, max_history=100)

        # On each TOB update:
        completed = agg.on_price(market_id, mid_price, ts)

        # On each trade:
        completed = agg.on_trade(market_id, price, size, ts)

        # completed is a list of newly closed Candle objects (usually 0 or 1).

        # Get candle history:
        candles = agg.get_history(market_id)       # list of closed candles, oldest first
        current = agg.get_current_candle(market_id) # in-progress candle snapshot
    """

    def __init__(self, interval_secs: float = 300.0, max_history: int = 200):
        self._interval = max(1.0, float(interval_secs))
        self._max_history = int(max_history)
        self._builders: dict[str, _CandleBuilder] = {}
        self._history: dict[str, list[Candle]] = {}

    @property
    def interval_secs(self) -> float:
        return self._interval

    def _bucket_start(self, ts: float) -> float:
        """Return the start timestamp for the candle bucket containing `ts`."""
        return math.floor(ts / self._interval) * self._interval

    def _ensure_builder(self, market_id: str, ts: float) -> tuple[_CandleBuilder, list[Candle]]:
        """
        Ensure there is an active builder for the current time bucket.
        Returns (builder, completed_candles).
        """
        completed: list[Candle] = []
        bucket_start = self._bucket_start(ts)
        bucket_end = bucket_start + self._interval

        b = self._builders.get(market_id)
        if b is not None and bucket_start >= b.close_ts:
            # Current builder belongs to a past bucket -> close it
            if b._started:
                candle = b.to_candle(complete=True)
                hist = self._history.setdefault(market_id, [])
                hist.append(candle)
                if len(hist) > self._max_history:
                    hist[:] = hist[-self._max_history:]
                completed.append(candle)
            b = None

        if b is None:
            b = _CandleBuilder(
                market_id=market_id,
                open_ts=bucket_start,
                close_ts=bucket_end,
            )
            self._builders[market_id] = b

        return b, completed

    def on_price(self, market_id: str, price: float, ts: float | None = None) -> list[Candle]:
        """
        Feed a mid-price update (from TOB). Returns list of newly completed candles.
        """
        if ts is None:
            ts = time.time()
        b, completed = self._ensure_builder(market_id, ts)
        b.update_price(price, volume=0.0, is_trade=False)
        return completed

    def on_trade(self, market_id: str, price: float, size: float, ts: float | None = None) -> list[Candle]:
        """
        Feed a trade tick. Returns list of newly completed candles.
        """
        if ts is None:
            ts = time.time()
        b, completed = self._ensure_builder(market_id, ts)
        b.update_price(price, volume=size, is_trade=True)
        return completed

    def get_history(self, market_id: str) -> list[Candle]:
        """Return closed candle history for a market (oldest first)."""
        return list(self._history.get(market_id, []))

    def get_current_candle(self, market_id: str) -> Candle | None:
        """Return a snapshot of the in-progress candle, or None."""
        b = self._builders.get(market_id)
        if b is None or not b._started:
            return None
        return b.to_candle(complete=False)

    def get_all_candles(self, market_id: str) -> list[Candle]:
        """Return all candles (closed + current in-progress) for a market."""
        candles = self.get_history(market_id)
        current = self.get_current_candle(market_id)
        if current is not None:
            candles.append(current)
        return candles
