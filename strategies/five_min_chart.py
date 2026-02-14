"""
5-Minute Chart Directional Strategy — Paper Trading

Proven approach combining multiple confluence signals on 5-minute candles:

Entry signals (LONG / buy):
  1. EMA crossover: 9 EMA crosses above 21 EMA (momentum shift)
  2. RSI confirmation: RSI(14) between 30-65 (not overbought)
  3. VWAP filter: price above VWAP (bullish bias)
  4. Trend filter: price above 50 EMA (higher timeframe alignment)

Entry signals (SHORT / sell):
  1. EMA crossover: 9 EMA crosses below 21 EMA
  2. RSI confirmation: RSI(14) between 35-70 (not oversold)
  3. VWAP filter: price below VWAP (bearish bias)
  4. Trend filter: price below 50 EMA

Exit management:
  - ATR-based stop loss: 1.5x ATR(14) from entry
  - ATR-based take profit: 2.0x ATR(14) from entry (1:1.33 R:R minimum)
  - Trailing stop after 1x ATR profit
  - Time-based exit: close position after max_hold_candles

Position sizing:
  - Fixed fractional: risk a configurable % of capital per trade
  - Size = risk_amount / (stop_distance)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from execution.base import OrderRequest
from strategies.base import Strategy, StrategyContext
from strategies.indicators import compute_signals, SignalSnapshot
from trading.candles import Candle, CandleAggregator
from utils.logging import get_logger


@dataclass
class TradeState:
    """Tracks an active paper position managed by this strategy."""
    market_id: str
    side: Literal["buy", "sell"]
    entry_price: float
    entry_ts: float
    stop_loss: float
    take_profit: float
    trailing_stop: float | None = None
    entry_candle_idx: int = 0
    atr_at_entry: float = 0.0


class FiveMinuteChartStrategy(Strategy):
    """
    Directional 5-minute chart strategy for paper trading.

    Uses EMA crossover as the primary signal with RSI, VWAP, and trend EMA
    as confluence filters. ATR-based risk management for exits.
    """
    name = "five_min_chart"

    def __init__(
        self,
        candle_aggregator: CandleAggregator,
        *,
        fast_ema: int = 9,
        slow_ema: int = 21,
        trend_ema: int = 50,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        atr_period: int = 14,
        atr_stop_mult: float = 1.5,
        atr_tp_mult: float = 2.0,
        atr_trail_trigger: float = 1.0,
        atr_trail_dist: float = 1.0,
        max_hold_candles: int = 24,
        min_candles_for_signal: int = 55,
        min_signal_cooldown_secs: float = 300.0,
        require_vwap_confluence: bool = True,
        require_trend_confluence: bool = True,
        require_rsi_confluence: bool = True,
    ):
        self._log = get_logger(__name__)
        self._candles = candle_aggregator
        self._fast_ema = fast_ema
        self._slow_ema = slow_ema
        self._trend_ema = trend_ema
        self._rsi_period = rsi_period
        self._rsi_oversold = rsi_oversold
        self._rsi_overbought = rsi_overbought
        self._atr_period = atr_period
        self._atr_stop_mult = atr_stop_mult
        self._atr_tp_mult = atr_tp_mult
        self._atr_trail_trigger = atr_trail_trigger
        self._atr_trail_dist = atr_trail_dist
        self._max_hold_candles = max_hold_candles
        self._min_candles = min_signal_cooldown_secs
        self._min_candles_for_signal = min_candles_for_signal
        self._require_vwap = require_vwap_confluence
        self._require_trend = require_trend_confluence
        self._require_rsi = require_rsi_confluence

        # Active trade states per market
        self._trades: dict[str, TradeState] = {}
        # Last signal timestamp per market (cooldown)
        self._last_signal_ts: dict[str, float] = {}
        # Count of closed candles seen per market (for position aging)
        self._candle_count: dict[str, int] = {}
        self._min_signal_cooldown_secs = min_signal_cooldown_secs

    def on_candle_closed(self, market_id: str, candle: Candle) -> None:
        """
        Called by the app when a 5m candle closes.
        Increments the internal candle counter for position age tracking.
        """
        self._candle_count[market_id] = self._candle_count.get(market_id, 0) + 1

    async def on_market(self, ctx: StrategyContext, market_id: str) -> None:
        """
        Main strategy tick. Called periodically for each active market.

        1. Compute signals from candle history
        2. Manage existing position (stop/TP/trail/time exit)
        3. Check for new entry signals
        """
        candles = self._candles.get_all_candles(market_id)
        if len(candles) < self._min_candles_for_signal:
            return

        # Compute full signal snapshot
        signals = compute_signals(
            candles,
            market_id,
            fast_ema_period=self._fast_ema,
            slow_ema_period=self._slow_ema,
            trend_ema_period=self._trend_ema,
            rsi_period=self._rsi_period,
            rsi_oversold=self._rsi_oversold,
            rsi_overbought=self._rsi_overbought,
        )
        if signals is None:
            return

        # Get current TOB for execution prices
        async with ctx.state.lock:
            tob = ctx.state.tob.get(market_id)
            m = ctx.state.markets.get(market_id)
        if tob is None or m is None:
            return
        if tob.best_bid is None or tob.best_ask is None:
            return

        # Persist the signal snapshot for dashboard/telemetry
        self._persist_signal(ctx, signals)

        # 1. Manage existing position
        active_trade = self._trades.get(market_id)
        if active_trade is not None:
            closed = await self._manage_position(ctx, market_id, active_trade, signals, tob)
            if closed:
                return  # Just closed a position, don't immediately re-enter

        # 2. Check for new entry (only if no active position in this market)
        if market_id not in self._trades:
            await self._check_entry(ctx, market_id, signals, tob, m)

    async def _manage_position(
        self, ctx: StrategyContext, market_id: str, trade: TradeState, signals: SignalSnapshot, tob
    ) -> bool:
        """
        Manage an active position: check stop loss, take profit, trailing stop, time exit.
        Returns True if position was closed.
        """
        current_price = signals.close
        now = time.time()

        close_reason: str | None = None
        close_price: float | None = None

        # Stop loss
        if trade.side == "buy" and current_price <= trade.stop_loss:
            close_reason = "stop_loss"
            close_price = float(tob.best_bid)
        elif trade.side == "sell" and current_price >= trade.stop_loss:
            close_reason = "stop_loss"
            close_price = float(tob.best_ask)

        # Take profit
        if close_reason is None:
            if trade.side == "buy" and current_price >= trade.take_profit:
                close_reason = "take_profit"
                close_price = float(tob.best_bid)
            elif trade.side == "sell" and current_price <= trade.take_profit:
                close_reason = "take_profit"
                close_price = float(tob.best_ask)

        # Trailing stop
        if close_reason is None and trade.trailing_stop is not None:
            if trade.side == "buy" and current_price <= trade.trailing_stop:
                close_reason = "trailing_stop"
                close_price = float(tob.best_bid)
            elif trade.side == "sell" and current_price >= trade.trailing_stop:
                close_reason = "trailing_stop"
                close_price = float(tob.best_ask)

        # Update trailing stop if price has moved favorably beyond trigger
        if close_reason is None and trade.atr_at_entry > 0:
            if trade.side == "buy":
                profit_distance = current_price - trade.entry_price
                if profit_distance >= trade.atr_at_entry * self._atr_trail_trigger:
                    new_trail = current_price - trade.atr_at_entry * self._atr_trail_dist
                    if trade.trailing_stop is None or new_trail > trade.trailing_stop:
                        trade.trailing_stop = new_trail
            else:  # sell
                profit_distance = trade.entry_price - current_price
                if profit_distance >= trade.atr_at_entry * self._atr_trail_trigger:
                    new_trail = current_price + trade.atr_at_entry * self._atr_trail_dist
                    if trade.trailing_stop is None or new_trail < trade.trailing_stop:
                        trade.trailing_stop = new_trail

        # Time-based exit: max hold candles
        if close_reason is None:
            candles_held = self._candle_count.get(market_id, 0) - trade.entry_candle_idx
            if candles_held >= self._max_hold_candles:
                close_reason = "max_hold_time"
                close_price = float(tob.best_bid) if trade.side == "buy" else float(tob.best_ask)

        # Counter-signal exit: if EMA crosses against us, exit early
        if close_reason is None:
            if trade.side == "buy" and signals.ema_cross_down:
                close_reason = "counter_signal"
                close_price = float(tob.best_bid)
            elif trade.side == "sell" and signals.ema_cross_up:
                close_reason = "counter_signal"
                close_price = float(tob.best_ask)

        if close_reason is not None and close_price is not None:
            await self._close_position(ctx, market_id, trade, close_price, close_reason)
            return True

        return False

    async def _close_position(
        self, ctx: StrategyContext, market_id: str, trade: TradeState, price: float, reason: str
    ) -> None:
        """Place a closing order and clean up trade state."""
        pos = ctx.portfolio.positions.get(market_id)
        if pos is None or pos.qty == 0:
            self._trades.pop(market_id, None)
            return

        close_side = "sell" if trade.side == "buy" else "buy"
        size = abs(float(pos.qty))

        async with ctx.state.lock:
            m = ctx.state.markets.get(market_id)
            tob = ctx.state.tob.get(market_id)
        event_id = m.event_id if m else f"event:{market_id}"

        r = ctx.risk.pre_trade_check(
            market_id=market_id,
            event_id=event_id,
            side=close_side,
            price=price,
            size=size,
            tob=tob,
            portfolio=ctx.portfolio,
        )
        if not r.ok:
            self._log.warning(
                "five_min.close_blocked",
                market_id=market_id,
                reason=reason,
                risk_reason=r.reason,
            )
            return

        await ctx.broker.cancel_all_market(market_id)
        await ctx.broker.place_limit(
            OrderRequest(
                market_id=market_id,
                side=close_side,
                price=price,
                size=size,
                meta={
                    "strategy": self.name,
                    "action": "close",
                    "reason": reason,
                    "entry_price": trade.entry_price,
                    "stop_loss": trade.stop_loss,
                    "take_profit": trade.take_profit,
                    "trailing_stop": trade.trailing_stop,
                },
            )
        )

        pnl_est = (price - trade.entry_price) * size if trade.side == "buy" else (trade.entry_price - price) * size
        self._log.info(
            "five_min.position_closed",
            market_id=market_id,
            side=trade.side,
            entry_price=trade.entry_price,
            exit_price=price,
            reason=reason,
            est_pnl=round(pnl_est, 6),
            hold_candles=self._candle_count.get(market_id, 0) - trade.entry_candle_idx,
        )
        self._trades.pop(market_id, None)

    async def _check_entry(
        self, ctx: StrategyContext, market_id: str, signals: SignalSnapshot, tob, m
    ) -> None:
        """
        Check for a new entry signal using confluence of indicators.
        """
        now = time.time()

        # Cooldown check
        last_sig = self._last_signal_ts.get(market_id, 0.0)
        if (now - last_sig) < self._min_signal_cooldown_secs:
            return

        # Need ATR for stop/TP sizing
        if signals.atr_value is None or signals.atr_value <= 0:
            return

        direction = self._evaluate_direction(signals)
        if direction is None:
            return

        side: Literal["buy", "sell"] = direction
        entry_price = float(tob.best_ask) if side == "buy" else float(tob.best_bid)
        atr_val = signals.atr_value

        # ATR-based stop and target
        if side == "buy":
            stop_loss = entry_price - atr_val * self._atr_stop_mult
            take_profit = entry_price + atr_val * self._atr_tp_mult
        else:
            stop_loss = entry_price + atr_val * self._atr_stop_mult
            take_profit = entry_price - atr_val * self._atr_tp_mult

        # Position sizing: use base_order_size from settings
        size = float(ctx.settings.base_order_size)

        # Risk check
        event_id = m.event_id if m else f"event:{market_id}"
        r = ctx.risk.pre_trade_check(
            market_id=market_id,
            event_id=event_id,
            side=side,
            price=entry_price,
            size=size,
            tob=tob,
            portfolio=ctx.portfolio,
        )
        if not r.ok:
            return

        # Place entry order
        await ctx.broker.place_limit(
            OrderRequest(
                market_id=market_id,
                side=side,
                price=entry_price,
                size=size,
                meta={
                    "strategy": self.name,
                    "action": "entry",
                    "signal_direction": side,
                    "ema_fast": signals.ema_fast,
                    "ema_slow": signals.ema_slow,
                    "rsi": signals.rsi_value,
                    "vwap": signals.vwap_value,
                    "atr": atr_val,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                },
            )
        )

        # Track the active trade
        self._trades[market_id] = TradeState(
            market_id=market_id,
            side=side,
            entry_price=entry_price,
            entry_ts=now,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_candle_idx=self._candle_count.get(market_id, 0),
            atr_at_entry=atr_val,
        )
        self._last_signal_ts[market_id] = now

        self._log.info(
            "five_min.entry_signal",
            market_id=market_id,
            side=side,
            entry_price=entry_price,
            stop_loss=round(stop_loss, 6),
            take_profit=round(take_profit, 6),
            atr=round(atr_val, 6),
            ema_fast=signals.ema_fast,
            ema_slow=signals.ema_slow,
            rsi=signals.rsi_value,
            vwap=signals.vwap_value,
            ema_cross_up=signals.ema_cross_up,
            ema_cross_down=signals.ema_cross_down,
        )

    def _evaluate_direction(self, signals: SignalSnapshot) -> Literal["buy", "sell"] | None:
        """
        Evaluate all confluence filters and return a direction or None.

        LONG entry requires:
          - EMA crossover up (9 EMA > 21 EMA, just crossed)
          - RSI not overbought (< 65 for conservative entry)
          - Price above VWAP (if required)
          - Price above 50 EMA trend filter (if required)

        SHORT entry requires:
          - EMA crossover down (9 EMA < 21 EMA, just crossed)
          - RSI not oversold (> 35 for conservative entry)
          - Price below VWAP (if required)
          - Price below 50 EMA trend filter (if required)
        """
        # --- LONG ---
        if signals.ema_cross_up:
            # RSI filter: not already overbought
            if self._require_rsi:
                if signals.rsi_value is None:
                    return None
                if signals.rsi_value > 65.0:
                    return None
                if signals.rsi_value < 20.0:
                    return None  # Too extreme, likely noise
            # VWAP filter: price should be above VWAP for bullish confirmation
            if self._require_vwap:
                if not signals.above_vwap:
                    return None
            # Trend filter: price above 50 EMA
            if self._require_trend:
                if signals.ema_trend is not None and signals.close < signals.ema_trend:
                    return None
            return "buy"

        # --- SHORT ---
        if signals.ema_cross_down:
            if self._require_rsi:
                if signals.rsi_value is None:
                    return None
                if signals.rsi_value < 35.0:
                    return None
                if signals.rsi_value > 80.0:
                    return None
            if self._require_vwap:
                if signals.above_vwap:
                    return None
            if self._require_trend:
                if signals.ema_trend is not None and signals.close > signals.ema_trend:
                    return None
            return "sell"

        return None

    def _persist_signal(self, ctx: StrategyContext, signals: SignalSnapshot) -> None:
        """Persist signal snapshot to store for dashboard/telemetry."""
        try:
            ctx.store.insert_signal_snapshot({
                "ts": signals.ts,
                "market_id": signals.market_id,
                "close": signals.close,
                "ema_fast": signals.ema_fast,
                "ema_slow": signals.ema_slow,
                "ema_trend": signals.ema_trend,
                "rsi": signals.rsi_value,
                "vwap": signals.vwap_value,
                "bb_upper": signals.bb_upper,
                "bb_middle": signals.bb_middle,
                "bb_lower": signals.bb_lower,
                "atr": signals.atr_value,
                "ema_cross_up": signals.ema_cross_up,
                "ema_cross_down": signals.ema_cross_down,
                "above_vwap": signals.above_vwap,
                "rsi_oversold": signals.rsi_oversold,
                "rsi_overbought": signals.rsi_overbought,
                "has_active_trade": signals.market_id in self._trades,
            })
        except Exception:
            pass  # Never break trading for telemetry

    def get_active_trades(self) -> dict[str, TradeState]:
        """Return copy of active trades for dashboard."""
        return dict(self._trades)
