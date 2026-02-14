"""
BTC/USD Price Feed for 5-minute chart analysis.

Fetches real BTC/USD price data to build candles and compute technical indicators.
The indicators then drive directional bets on Polymarket BTC Up/Down 5m markets.

Data sources (in priority order):
1. CoinGecko public API (no key needed, generous rate limits)
2. Binance public API (klines endpoint)
3. Fallback: mock feed for offline testing

The feed emits periodic price snapshots that the CandleAggregator
converts into 5-minute OHLCV bars.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator

import aiohttp

from utils.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class BtcPriceSnapshot:
    """A single BTC/USD price observation."""
    price: float
    source: str
    ts: float


@dataclass(frozen=True)
class BtcOHLCV:
    """A single BTC/USD OHLCV candle from an exchange."""
    open_ts: float
    close_ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float


class BtcPriceFeed:
    """
    Streams BTC/USD price snapshots at a configurable interval.

    Used by the candle aggregator to build 5-minute bars for indicator computation.
    """

    def __init__(self, poll_secs: float = 5.0):
        self._poll = max(1.0, float(poll_secs))
        self._log = get_logger(__name__)
        self._last_price: float | None = None

    async def _fetch_binance_price(self, session: aiohttp.ClientSession) -> float | None:
        """Fetch BTC/USDT spot price from Binance public API."""
        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            params = {"symbol": "BTCUSDT"}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["price"])
        except Exception:
            pass
        return None

    async def _fetch_coingecko_price(self, session: aiohttp.ClientSession) -> float | None:
        """Fetch BTC/USD price from CoinGecko public API."""
        try:
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {"ids": "bitcoin", "vs_currencies": "usd"}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["bitcoin"]["usd"])
        except Exception:
            pass
        return None

    async def _fetch_price(self) -> BtcPriceSnapshot | None:
        """Try all sources, return the first successful one."""
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Try Binance first (most reliable, fastest)
            price = await self._fetch_binance_price(session)
            if price is not None:
                self._last_price = price
                return BtcPriceSnapshot(price=price, source="binance", ts=time.time())

            # Fallback to CoinGecko
            price = await self._fetch_coingecko_price(session)
            if price is not None:
                self._last_price = price
                return BtcPriceSnapshot(price=price, source="coingecko", ts=time.time())

        return None

    async def stream_prices(self) -> AsyncIterator[BtcPriceSnapshot]:
        """
        Yield BTC/USD price snapshots at the configured interval.
        Retries silently on failure; never crashes the generator.
        """
        while True:
            try:
                snap = await self._fetch_price()
                if snap is not None:
                    yield snap
                else:
                    self._log.warning("btc_price_feed.all_sources_failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("btc_price_feed.error")
            await asyncio.sleep(self._poll)

    @property
    def last_price(self) -> float | None:
        return self._last_price


class BtcKlineFeed:
    """
    Fetches historical BTC/USD 5-minute klines from Binance to bootstrap
    candle history instantly (instead of waiting hours for candles to build up).
    """

    def __init__(self):
        self._log = get_logger(__name__)

    async def fetch_klines(self, interval: str = "5m", limit: int = 200) -> list[BtcOHLCV]:
        """
        Fetch historical klines from Binance.
        interval: "1m", "5m", "15m", "1h", etc.
        limit: number of candles (max 1000)
        """
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": "BTCUSDT",
            "interval": interval,
            "limit": str(min(limit, 1000)),
        }
        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            candles: list[BtcOHLCV] = []
            for k in data:
                # Binance kline format: [open_time, open, high, low, close, volume, close_time, ...]
                candles.append(BtcOHLCV(
                    open_ts=float(k[0]) / 1000.0,   # ms -> s
                    close_ts=float(k[6]) / 1000.0,   # ms -> s
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                ))

            self._log.info("btc_kline_feed.fetched", count=len(candles), interval=interval)
            return candles

        except Exception:
            self._log.exception("btc_kline_feed.error")
            return []


class MockBtcPriceFeed:
    """
    Mock BTC price feed for offline testing.
    Generates synthetic BTC-like price movements.
    """

    def __init__(self, poll_secs: float = 5.0, seed: int = 42, base_price: float = 97000.0):
        import random
        self._poll = max(0.5, float(poll_secs))
        self._rng = random.Random(seed)
        self._price = base_price
        self._last_price = base_price

    async def stream_prices(self) -> AsyncIterator[BtcPriceSnapshot]:
        while True:
            # Random walk with slight mean reversion
            change = self._rng.gauss(0, 50)  # ~$50 stddev per tick
            self._price += change
            self._price = max(10000.0, self._price)  # floor
            self._last_price = self._price
            yield BtcPriceSnapshot(
                price=self._price,
                source="mock",
                ts=time.time(),
            )
            await asyncio.sleep(self._poll)

    @property
    def last_price(self) -> float | None:
        return self._last_price
