"""Tests for the 5-minute candle aggregator."""
from __future__ import annotations

import math

from trading.candles import Candle, CandleAggregator


def test_single_price_creates_builder():
    agg = CandleAggregator(interval_secs=300, max_history=100)
    completed = agg.on_price("m1", 0.50, ts=1000.0)
    assert completed == []
    current = agg.get_current_candle("m1")
    assert current is not None
    assert current.open == 0.50
    assert current.close == 0.50
    assert current.high == 0.50
    assert current.low == 0.50
    assert current.volume == 0.0
    assert not current.complete


def test_candle_completes_on_boundary():
    agg = CandleAggregator(interval_secs=300, max_history=100)
    # Bucket [0, 300)
    agg.on_price("m1", 0.50, ts=0.0)
    agg.on_price("m1", 0.55, ts=100.0)
    agg.on_price("m1", 0.45, ts=200.0)
    agg.on_price("m1", 0.52, ts=299.0)

    # Cross into next bucket [300, 600)
    completed = agg.on_price("m1", 0.53, ts=300.0)
    assert len(completed) == 1
    c = completed[0]
    assert c.complete is True
    assert c.open == 0.50
    assert c.high == 0.55
    assert c.low == 0.45
    assert c.close == 0.52
    assert c.open_ts == 0.0
    assert c.close_ts == 300.0


def test_history_accumulates():
    agg = CandleAggregator(interval_secs=60, max_history=100)
    # Create 5 complete candles
    for i in range(5):
        ts_start = i * 60.0
        agg.on_price("m1", 0.50 + i * 0.01, ts=ts_start + 10.0)
        agg.on_price("m1", 0.51 + i * 0.01, ts=ts_start + 30.0)
    # Close the 5th candle by entering 6th bucket
    agg.on_price("m1", 0.60, ts=300.0)

    history = agg.get_history("m1")
    assert len(history) == 5
    assert all(c.complete for c in history)


def test_max_history_trims():
    agg = CandleAggregator(interval_secs=10, max_history=3)
    for i in range(10):
        agg.on_price("m1", 0.50 + i * 0.001, ts=i * 10.0 + 5.0)
    # Close last by entering next bucket
    agg.on_price("m1", 0.60, ts=100.0)

    history = agg.get_history("m1")
    assert len(history) <= 3


def test_trade_adds_volume():
    agg = CandleAggregator(interval_secs=300, max_history=100)
    agg.on_price("m1", 0.50, ts=0.0)
    agg.on_trade("m1", 0.51, 100.0, ts=10.0)
    agg.on_trade("m1", 0.49, 50.0, ts=20.0)

    current = agg.get_current_candle("m1")
    assert current is not None
    assert current.volume == 150.0
    assert current.trade_count == 2
    assert current.high == 0.51
    assert current.low == 0.49


def test_vwap_calculation():
    agg = CandleAggregator(interval_secs=300, max_history=100)
    # Two trades: 100 @ 0.50, 200 @ 0.60
    # VWAP = (100*0.50 + 200*0.60) / (300) = 170/300 = 0.5667
    agg.on_trade("m1", 0.50, 100.0, ts=10.0)
    agg.on_trade("m1", 0.60, 200.0, ts=20.0)

    current = agg.get_current_candle("m1")
    assert current is not None
    expected_vwap = (100 * 0.50 + 200 * 0.60) / 300.0
    assert abs(current.vwap - expected_vwap) < 1e-6


def test_multiple_markets():
    agg = CandleAggregator(interval_secs=300, max_history=100)
    agg.on_price("m1", 0.50, ts=0.0)
    agg.on_price("m2", 0.70, ts=0.0)
    agg.on_price("m1", 0.51, ts=100.0)
    agg.on_price("m2", 0.71, ts=100.0)

    c1 = agg.get_current_candle("m1")
    c2 = agg.get_current_candle("m2")
    assert c1 is not None and c2 is not None
    assert c1.close == 0.51
    assert c2.close == 0.71


def test_get_all_candles_includes_current():
    agg = CandleAggregator(interval_secs=60, max_history=100)
    agg.on_price("m1", 0.50, ts=5.0)
    agg.on_price("m1", 0.51, ts=30.0)
    # Close first candle
    agg.on_price("m1", 0.52, ts=65.0)

    all_candles = agg.get_all_candles("m1")
    assert len(all_candles) == 2
    assert all_candles[0].complete is True
    assert all_candles[1].complete is False


def test_no_candle_for_unseen_market():
    agg = CandleAggregator(interval_secs=300, max_history=100)
    assert agg.get_current_candle("unknown") is None
    assert agg.get_history("unknown") == []
    assert agg.get_all_candles("unknown") == []


def test_candle_as_dict():
    c = Candle(
        market_id="m1",
        open_ts=0.0,
        close_ts=300.0,
        open=0.50,
        high=0.55,
        low=0.45,
        close=0.52,
        volume=100.0,
        trade_count=5,
        vwap=0.51,
        complete=True,
    )
    d = c.as_dict()
    assert d["market_id"] == "m1"
    assert d["open"] == 0.50
    assert d["complete"] is True
