"""Tests for SQLite candle and signal snapshot persistence."""
from __future__ import annotations

import time

import pytest

from storage.sqlite import SqliteStore


@pytest.fixture
def store():
    s = SqliteStore(":memory:")
    s.init_db()
    return s


def test_insert_candle(store):
    store.insert_candle({
        "market_id": "m1",
        "open_ts": 0.0,
        "close_ts": 300.0,
        "open": 0.50,
        "high": 0.55,
        "low": 0.45,
        "close": 0.52,
        "volume": 1000.0,
        "trade_count": 50,
        "vwap": 0.51,
        "complete": True,
    })
    candles = store.fetch_recent_candles("m1", limit=10)
    assert len(candles) == 1
    c = candles[0]
    assert c["market_id"] == "m1"
    assert c["open"] == 0.50
    assert c["high"] == 0.55
    assert c["low"] == 0.45
    assert c["close"] == 0.52
    assert c["volume"] == 1000.0
    assert c["complete"] == 1


def test_insert_multiple_candles(store):
    for i in range(5):
        store.insert_candle({
            "market_id": "m1",
            "open_ts": float(i * 300),
            "close_ts": float((i + 1) * 300),
            "open": 0.50 + i * 0.01,
            "high": 0.55 + i * 0.01,
            "low": 0.45 + i * 0.01,
            "close": 0.52 + i * 0.01,
            "volume": 100.0,
            "trade_count": 10,
            "vwap": 0.51 + i * 0.01,
            "complete": True,
        })
    candles = store.fetch_recent_candles("m1", limit=100)
    assert len(candles) == 5
    # Should be ordered by open_ts DESC
    assert candles[0]["open_ts"] == 1200.0
    assert candles[-1]["open_ts"] == 0.0


def test_insert_signal_snapshot(store):
    store.insert_signal_snapshot({
        "ts": time.time(),
        "market_id": "m1",
        "close": 0.52,
        "ema_fast": 0.51,
        "ema_slow": 0.50,
        "ema_trend": 0.49,
        "rsi": 55.0,
        "vwap": 0.51,
        "bb_upper": 0.55,
        "bb_middle": 0.50,
        "bb_lower": 0.45,
        "atr": 0.02,
        "ema_cross_up": True,
        "ema_cross_down": False,
        "above_vwap": True,
        "rsi_oversold": False,
        "rsi_overbought": False,
        "has_active_trade": False,
    })
    signals = store.fetch_latest_signals(limit=10)
    assert len(signals) == 1
    s = signals[0]
    assert s["market_id"] == "m1"
    assert s["ema_fast"] == 0.51
    assert s["rsi"] == 55.0
    assert s["ema_cross_up"] == 1
    assert s["ema_cross_down"] == 0


def test_fetch_latest_signals_per_market(store):
    """Should return only the latest signal per market."""
    for i in range(3):
        store.insert_signal_snapshot({
            "ts": float(i),
            "market_id": "m1",
            "close": 0.50 + i * 0.01,
            "ema_fast": 0.50,
            "ema_slow": 0.49,
            "rsi": 50.0 + i,
        })
    signals = store.fetch_latest_signals(limit=10)
    assert len(signals) == 1
    assert signals[0]["close"] == 0.52  # latest


def test_candle_different_markets(store):
    store.insert_candle({
        "market_id": "m1",
        "open_ts": 0.0, "close_ts": 300.0,
        "open": 0.50, "high": 0.55, "low": 0.45, "close": 0.52,
        "volume": 100.0, "trade_count": 10, "vwap": 0.51, "complete": True,
    })
    store.insert_candle({
        "market_id": "m2",
        "open_ts": 0.0, "close_ts": 300.0,
        "open": 0.70, "high": 0.75, "low": 0.65, "close": 0.72,
        "volume": 200.0, "trade_count": 20, "vwap": 0.71, "complete": True,
    })
    assert len(store.fetch_recent_candles("m1")) == 1
    assert len(store.fetch_recent_candles("m2")) == 1
    assert store.fetch_recent_candles("m1")[0]["open"] == 0.50
    assert store.fetch_recent_candles("m2")[0]["open"] == 0.70
