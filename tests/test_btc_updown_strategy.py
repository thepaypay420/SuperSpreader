"""Tests for BTC Up/Down 5-minute binary strategy."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from connectors.polymarket.btc_5m_discovery import BtcUpDownMarket
from execution.paper import PaperBroker
from risk.portfolio import Portfolio
from risk.rules import RiskEngine
from storage.sqlite import SqliteStore
from strategies.base import StrategyContext
from strategies.btc_updown import BtcUpDownStrategy, BtcBetRecord
from strategies.indicators import SignalSnapshot
from trading.candles import Candle, CandleAggregator
from trading.state import SharedState
from trading.types import MarketInfo, TopOfBook


@pytest.fixture
def store():
    s = SqliteStore(":memory:")
    s.init_db()
    return s


@pytest.fixture
def settings():
    @dataclass
    class FakeSettings:
        trade_mode: str = "paper"
        execution_mode: str = "paper"
        base_order_size: float = 10.0
        max_pos_per_market: float = 200.0
        max_open_positions: int = 10
        max_event_exposure: float = 500.0
        daily_loss_limit: float = 200.0
        kill_switch: bool = False
        max_feed_lag_secs: float = 300.0
        max_spread: float = 0.20
        fees_bps: float = 0.0
        slippage_bps: float = 0.0
        latency_bps: float = 0.0
        stop_before_end_secs: float = 3600.0
    return FakeSettings()


@pytest.fixture
def candle_agg():
    return CandleAggregator(interval_secs=300, max_history=200)


@pytest.fixture
def strategy(candle_agg):
    return BtcUpDownStrategy(
        candle_agg,
        fast_ema=9,
        slow_ema=21,
        trend_ema=50,
        rsi_period=14,
        min_candles_for_signal=55,
        min_confidence=0.50,
        require_vwap=False,
        require_trend=False,
        require_rsi=False,
    )


def _make_btc_market(market_id: str = "12345") -> BtcUpDownMarket:
    return BtcUpDownMarket(
        market_id=market_id,
        event_slug=f"btc-updown-5m-test-{market_id}",
        question="Bitcoin Up or Down - Test",
        event_id=f"evt-{market_id}",
        condition_id="0xabc",
        up_token_id="tok-up-123",
        down_token_id="tok-down-456",
        event_start_ts=time.time() + 60,
        event_end_ts=time.time() + 360,
        best_bid=0.49,
        best_ask=0.51,
        accepting_orders=True,
        active=True,
        closed=False,
        volume=5000.0,
    )


def _feed_btc_candles(agg: CandleAggregator, n: int = 60, trend: str = "up"):
    """Feed synthetic BTC candles into the aggregator."""
    btc_id = "btc_usd"
    base = 97000.0
    for i in range(n):
        ts = i * 300.0 + 10.0
        if trend == "up":
            price = base + i * 50.0  # steady uptrend
        elif trend == "down":
            price = base - i * 50.0
        else:
            price = base + (50.0 if i % 2 == 0 else -50.0)  # choppy

        agg.on_price(btc_id, price, ts=ts)
        agg.on_trade(btc_id, price + 10, 1.0, ts=ts + 1.0)
        agg.on_trade(btc_id, price - 10, 1.0, ts=ts + 2.0)
    # Close last candle
    final_price = base + n * 50.0 if trend == "up" else base - n * 50.0
    agg.on_price(btc_id, final_price, ts=n * 300.0 + 10.0)


def test_strategy_name(strategy):
    assert strategy.name == "btc_updown_5m"


def test_no_bets_initially(strategy):
    assert strategy.get_bet_history() == {}
    stats = strategy.get_stats()
    assert stats["total_bets"] == 0


def test_evaluate_direction_uptrend(strategy):
    """In an uptrend, should predict 'up' with decent confidence."""
    signals = SignalSnapshot(
        ts=time.time(),
        market_id="btc_usd",
        close=98000.0,
        ema_fast=98000.0,
        ema_slow=97500.0,
        ema_trend=97000.0,
        rsi_value=55.0,
        vwap_value=97800.0,
        atr_value=200.0,
        ema_cross_up=False,
        ema_cross_down=False,
        above_vwap=True,
    )
    direction, confidence = strategy._evaluate_direction(signals)
    assert direction == "up"
    assert confidence >= 0.50


def test_evaluate_direction_downtrend(strategy):
    """In a downtrend, should predict 'down'."""
    signals = SignalSnapshot(
        ts=time.time(),
        market_id="btc_usd",
        close=96000.0,
        ema_fast=96000.0,
        ema_slow=96500.0,
        ema_trend=97000.0,
        rsi_value=45.0,
        vwap_value=96200.0,
        atr_value=200.0,
        ema_cross_up=False,
        ema_cross_down=False,
        above_vwap=False,
    )
    direction, confidence = strategy._evaluate_direction(signals)
    assert direction == "down"
    assert confidence >= 0.50


def test_evaluate_direction_no_ema():
    """With no EMA data, should return None."""
    agg = CandleAggregator(interval_secs=300, max_history=200)
    strat = BtcUpDownStrategy(agg, min_confidence=0.50)
    signals = SignalSnapshot(
        ts=time.time(), market_id="btc_usd", close=97000.0,
        ema_fast=None, ema_slow=None,
    )
    direction, confidence = strat._evaluate_direction(signals)
    assert direction is None


def test_evaluate_direction_rsi_filter():
    """RSI extreme should block signal when filter is on."""
    agg = CandleAggregator(interval_secs=300, max_history=200)
    strat = BtcUpDownStrategy(agg, min_confidence=0.50, require_rsi=True)
    # Bullish EMA but RSI > 75 (overbought filter blocks)
    signals = SignalSnapshot(
        ts=time.time(), market_id="btc_usd", close=98000.0,
        ema_fast=98000.0, ema_slow=97500.0,
        rsi_value=80.0,
        ema_cross_up=False, ema_cross_down=False,
    )
    direction, confidence = strat._evaluate_direction(signals)
    assert direction is None


def test_confidence_increases_with_confluence(strategy):
    """More indicator agreement should increase confidence."""
    # Minimal signal (just EMA alignment)
    signals_weak = SignalSnapshot(
        ts=time.time(), market_id="btc_usd", close=98000.0,
        ema_fast=98000.0, ema_slow=97999.0,  # barely bullish
        rsi_value=50.0,
        ema_cross_up=False, ema_cross_down=False,
    )
    _, conf_weak = strategy._evaluate_direction(signals_weak)

    # Strong signal (EMA + RSI + VWAP + trend + cross)
    signals_strong = SignalSnapshot(
        ts=time.time(), market_id="btc_usd", close=99000.0,
        ema_fast=99000.0, ema_slow=98000.0,  # wide EMA gap
        ema_trend=97000.0,
        rsi_value=60.0,
        vwap_value=98500.0,
        atr_value=500.0,
        ema_cross_up=True, ema_cross_down=False,
        above_vwap=True,
    )
    _, conf_strong = strategy._evaluate_direction(signals_strong)

    assert conf_strong > conf_weak


@pytest.mark.asyncio
async def test_evaluate_and_bet_insufficient_candles(strategy, store, settings, candle_agg):
    """Should not bet without enough candle history."""
    from connectors.external_odds.disabled import DisabledOddsProvider

    state = SharedState()
    state.candle_aggregator = candle_agg
    portfolio = Portfolio()
    risk = RiskEngine(settings)
    broker = PaperBroker(store)
    odds = DisabledOddsProvider()
    ctx = StrategyContext(settings=settings, state=state, store=store,
                          broker=broker, risk=risk, portfolio=portfolio, odds=odds)

    # Only 10 candles (need 55)
    _feed_btc_candles(candle_agg, n=10)
    btc_market = _make_btc_market()
    result = await strategy.evaluate_and_bet(ctx, btc_market)
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_and_bet_with_data(strategy, store, settings, candle_agg):
    """Should attempt a bet with enough candle data."""
    from connectors.external_odds.disabled import DisabledOddsProvider

    state = SharedState()
    state.candle_aggregator = candle_agg
    portfolio = Portfolio()
    risk = RiskEngine(settings)
    broker = PaperBroker(store)
    odds = DisabledOddsProvider()
    ctx = StrategyContext(settings=settings, state=state, store=store,
                          broker=broker, risk=risk, portfolio=portfolio, odds=odds)

    # Build 60 candles with uptrend
    _feed_btc_candles(candle_agg, n=60, trend="up")

    btc_market = _make_btc_market("99999")

    # Register market in state (normally done by the betting loop)
    async with state.lock:
        state.markets[btc_market.market_id] = btc_market.to_market_info()
        state.tob[btc_market.market_id] = TopOfBook(
            best_bid=0.49, best_bid_size=1000.0,
            best_ask=0.51, best_ask_size=1000.0,
            ts=time.time(),
        )

    result = await strategy.evaluate_and_bet(ctx, btc_market)
    # May or may not place a bet depending on indicator values,
    # but should not crash
    assert result is None or isinstance(result, BtcBetRecord)


@pytest.mark.asyncio
async def test_no_double_bet(strategy, store, settings, candle_agg):
    """Should not bet the same market twice."""
    from connectors.external_odds.disabled import DisabledOddsProvider

    state = SharedState()
    state.candle_aggregator = candle_agg
    portfolio = Portfolio()
    risk = RiskEngine(settings)
    broker = PaperBroker(store)
    odds = DisabledOddsProvider()
    ctx = StrategyContext(settings=settings, state=state, store=store,
                          broker=broker, risk=risk, portfolio=portfolio, odds=odds)

    _feed_btc_candles(candle_agg, n=60, trend="up")
    btc_market = _make_btc_market("77777")

    async with state.lock:
        state.markets[btc_market.market_id] = btc_market.to_market_info()
        state.tob[btc_market.market_id] = TopOfBook(
            best_bid=0.49, best_bid_size=1000.0,
            best_ask=0.51, best_ask_size=1000.0,
            ts=time.time(),
        )

    # First call
    result1 = await strategy.evaluate_and_bet(ctx, btc_market)
    # Second call should be blocked (same market)
    result2 = await strategy.evaluate_and_bet(ctx, btc_market)
    if result1 is not None:
        assert result2 is None  # double bet blocked


def test_btc_updown_market_to_market_info():
    m = _make_btc_market()
    info = m.to_market_info()
    assert info.market_id == m.market_id
    assert info.event_id == m.event_id
    assert info.clob_token_id == m.up_token_id
