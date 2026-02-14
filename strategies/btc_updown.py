"""
BTC Up/Down 5-Minute Binary Strategy — Paper Trading.

This strategy:
1. Builds 5-minute candles from a real BTC/USD price feed
2. Computes technical indicators (EMA crossover, RSI, VWAP, ATR)
3. Predicts whether BTC will go UP or DOWN in the next 5-minute window
4. Places a bet on the corresponding Polymarket "BTC Up or Down" market

How it works:
- The Polymarket BTC 5m series creates a new binary market every 5 minutes
- Each market has outcomes "Up" and "Down" with token IDs
- "Up" price ≈ probability BTC goes up in that 5-minute window
- We buy "Up" tokens if our indicators say bullish, "Down" tokens if bearish
- Market resolves in 5 minutes; position auto-settles

Signal generation (proven 5m chart methods):
- PRIMARY: EMA(9) vs EMA(21) crossover direction
- CONFIRMATION: RSI(14) momentum filter
- BIAS: Price vs VWAP (above = bullish, below = bearish)
- TREND: EMA(50) as longer-term direction filter
- CONFIDENCE: ATR(14) for volatility regime detection
  (higher ATR = stronger moves = higher confidence bets)

Position sizing:
- Base size from settings
- Confidence multiplier based on indicator agreement
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Literal

from connectors.polymarket.btc_5m_discovery import BtcUpDownMarket
from execution.base import OrderRequest
from strategies.base import Strategy, StrategyContext
from strategies.indicators import compute_signals, SignalSnapshot
from trading.candles import CandleAggregator
from utils.logging import get_logger


@dataclass
class BtcBetRecord:
    """Record of a bet placed on a BTC 5m up/down market."""
    market_id: str
    event_slug: str
    direction: Literal["up", "down"]
    confidence: float
    entry_price: float
    size: float
    entry_ts: float
    event_start_ts: float | None
    event_end_ts: float | None
    signal_summary: dict[str, Any]


class BtcUpDownStrategy(Strategy):
    """
    Directional strategy for Polymarket BTC 5-minute Up/Down binary markets.

    Uses technical analysis on real BTC/USD 5m candles to predict direction,
    then places a bet on the next available market.
    """
    name = "btc_updown_5m"

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
        min_candles_for_signal: int = 55,
        min_confidence: float = 0.55,
        require_vwap: bool = True,
        require_trend: bool = True,
        require_rsi: bool = True,
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
        self._min_candles = min_candles_for_signal
        self._min_confidence = min_confidence
        self._require_vwap = require_vwap
        self._require_trend = require_trend
        self._require_rsi = require_rsi

        # Track bets to avoid double-betting the same market
        self._bet_history: dict[str, BtcBetRecord] = {}
        # The single virtual market_id we use for BTC price candles
        self.BTC_CANDLE_MARKET_ID = "btc_usd"

    async def on_market(self, ctx: StrategyContext, market_id: str) -> None:
        """
        Called by the main strategy loop. For BTC up/down, market_id is the
        Polymarket market ID of the current BTC 5m event.

        The strategy reads BTC candles from the shared aggregator,
        computes signals, and places a directional bet.
        """
        # This is called with the Polymarket market_id, but our candle
        # data is indexed under BTC_CANDLE_MARKET_ID
        pass  # Main logic is in evaluate_and_bet()

    async def evaluate_and_bet(
        self,
        ctx: StrategyContext,
        btc_market: BtcUpDownMarket,
    ) -> BtcBetRecord | None:
        """
        Evaluate BTC indicators and place a bet on the given market.

        Returns the bet record if a bet was placed, None otherwise.
        """
        market_id = btc_market.market_id

        # Don't bet the same market twice
        if market_id in self._bet_history:
            return None

        # Get BTC candle history
        candles = self._candles.get_all_candles(self.BTC_CANDLE_MARKET_ID)
        if len(candles) < self._min_candles:
            self._log.info(
                "btc_updown.insufficient_candles",
                have=len(candles),
                need=self._min_candles,
            )
            return None

        # Compute signals
        signals = compute_signals(
            candles,
            self.BTC_CANDLE_MARKET_ID,
            fast_ema_period=self._fast_ema,
            slow_ema_period=self._slow_ema,
            trend_ema_period=self._trend_ema,
            rsi_period=self._rsi_period,
            rsi_oversold=self._rsi_oversold,
            rsi_overbought=self._rsi_overbought,
        )
        if signals is None:
            return None

        # Persist signal for telemetry
        self._persist_signal(ctx, signals, btc_market)

        # Evaluate direction and confidence
        direction, confidence = self._evaluate_direction(signals)
        if direction is None:
            self._log.info(
                "btc_updown.no_signal",
                market_id=market_id,
                ema_fast=signals.ema_fast,
                ema_slow=signals.ema_slow,
                rsi=signals.rsi_value,
            )
            return None

        if confidence < self._min_confidence:
            self._log.info(
                "btc_updown.low_confidence",
                market_id=market_id,
                direction=direction,
                confidence=round(confidence, 3),
                min_required=self._min_confidence,
            )
            return None

        # Determine which side to buy on Polymarket
        # If direction == "up": buy "Up" tokens (side="buy" on the market)
        # If direction == "down": buy "Down" tokens
        # In Polymarket binary markets, the "Up" token price = P(up)
        # Buying "Up" at ask means we think P(up) > ask price
        # Buying "Down" means selling "Up" (or buying "Down" token directly)

        async with ctx.state.lock:
            tob = ctx.state.tob.get(market_id)
            m = ctx.state.markets.get(market_id)

        if tob is None or m is None:
            return None
        if tob.best_bid is None or tob.best_ask is None:
            return None

        # For "Up" bet: buy at the ask (we're buying the Up token)
        # For "Down" bet: sell at the bid (we're selling the Up token = buying Down)
        if direction == "up":
            side = "buy"
            price = float(tob.best_ask)
        else:
            side = "sell"
            price = float(tob.best_bid)

        size = float(ctx.settings.base_order_size)

        # Risk check
        event_id = m.event_id if m else f"event:{market_id}"
        r = ctx.risk.pre_trade_check(
            market_id=market_id,
            event_id=event_id,
            side=side,
            price=price,
            size=size,
            tob=tob,
            portfolio=ctx.portfolio,
        )
        if not r.ok:
            self._log.info(
                "btc_updown.risk_blocked",
                market_id=market_id,
                direction=direction,
                reason=r.reason,
            )
            return None

        # Place the bet
        signal_summary = {
            "direction": direction,
            "confidence": round(confidence, 3),
            "ema_fast": signals.ema_fast,
            "ema_slow": signals.ema_slow,
            "ema_trend": signals.ema_trend,
            "rsi": signals.rsi_value,
            "vwap": signals.vwap_value,
            "atr": signals.atr_value,
            "above_vwap": signals.above_vwap,
            "ema_cross_up": signals.ema_cross_up,
            "ema_cross_down": signals.ema_cross_down,
            "btc_close": signals.close,
        }

        await ctx.broker.place_limit(
            OrderRequest(
                market_id=market_id,
                side=side,
                price=price,
                size=size,
                meta={
                    "strategy": self.name,
                    "direction": direction,
                    "confidence": round(confidence, 3),
                    "event_slug": btc_market.event_slug,
                    "event_start": btc_market.event_start_ts,
                    "event_end": btc_market.event_end_ts,
                    **signal_summary,
                },
            )
        )

        record = BtcBetRecord(
            market_id=market_id,
            event_slug=btc_market.event_slug,
            direction=direction,
            confidence=confidence,
            entry_price=price,
            size=size,
            entry_ts=time.time(),
            event_start_ts=btc_market.event_start_ts,
            event_end_ts=btc_market.event_end_ts,
            signal_summary=signal_summary,
        )
        self._bet_history[market_id] = record

        self._log.info(
            "btc_updown.bet_placed",
            market_id=market_id,
            event_slug=btc_market.event_slug,
            direction=direction,
            confidence=round(confidence, 3),
            side=side,
            price=price,
            size=size,
            btc_price=signals.close,
            event_start=btc_market.event_start_ts,
            event_end=btc_market.event_end_ts,
        )
        return record

    def _evaluate_direction(
        self, signals: SignalSnapshot
    ) -> tuple[Literal["up", "down"] | None, float]:
        """
        Evaluate indicator confluence to determine direction and confidence.

        Confidence scoring (0 to 1):
        - Base: 0.50 (coin flip)
        - EMA alignment: +0.15
        - RSI confirmation: +0.10
        - VWAP confirmation: +0.10
        - Trend confirmation: +0.10
        - Recent EMA cross: +0.05 bonus

        Returns (direction, confidence) or (None, 0.0).
        """
        if signals.ema_fast is None or signals.ema_slow is None:
            return None, 0.0

        # Primary signal: EMA fast vs slow alignment
        ema_bullish = signals.ema_fast > signals.ema_slow
        ema_bearish = signals.ema_fast < signals.ema_slow

        if not ema_bullish and not ema_bearish:
            return None, 0.0  # EMAs are exactly equal (rare)

        direction: Literal["up", "down"] = "up" if ema_bullish else "down"
        confidence = 0.50

        # EMA spread strength: larger gap = stronger signal
        ema_gap = abs(signals.ema_fast - signals.ema_slow)
        if signals.close > 0:
            ema_gap_pct = ema_gap / signals.close
            # Scale: 0.1% gap = modest, 0.5%+ = strong
            confidence += min(0.15, ema_gap_pct * 30.0)
        else:
            confidence += 0.05

        # RSI confirmation
        if signals.rsi_value is not None:
            if direction == "up" and signals.rsi_value > 50 and signals.rsi_value < 70:
                confidence += 0.10
            elif direction == "down" and signals.rsi_value < 50 and signals.rsi_value > 30:
                confidence += 0.10
            # Extreme RSI opposing our direction = reduce confidence
            if direction == "up" and signals.rsi_value > 80:
                confidence -= 0.10
            elif direction == "down" and signals.rsi_value < 20:
                confidence -= 0.10
            # Filter: if RSI strongly contradicts, skip
            if self._require_rsi:
                if direction == "up" and signals.rsi_value > 75:
                    return None, 0.0
                if direction == "down" and signals.rsi_value < 25:
                    return None, 0.0

        # VWAP confirmation
        if signals.vwap_value is not None:
            if direction == "up" and signals.above_vwap:
                confidence += 0.10
            elif direction == "down" and not signals.above_vwap:
                confidence += 0.10
            elif self._require_vwap:
                # VWAP disagrees
                return None, 0.0

        # Trend EMA confirmation (50 EMA)
        if signals.ema_trend is not None:
            if direction == "up" and signals.close > signals.ema_trend:
                confidence += 0.10
            elif direction == "down" and signals.close < signals.ema_trend:
                confidence += 0.10
            elif self._require_trend:
                return None, 0.0

        # Bonus: recent EMA crossover (momentum shift)
        if direction == "up" and signals.ema_cross_up:
            confidence += 0.05
        elif direction == "down" and signals.ema_cross_down:
            confidence += 0.05

        confidence = max(0.0, min(1.0, confidence))
        return direction, confidence

    def _persist_signal(
        self, ctx: StrategyContext, signals: SignalSnapshot, btc_market: BtcUpDownMarket
    ) -> None:
        """Persist signal to SQLite for dashboard/analysis."""
        try:
            ctx.store.insert_signal_snapshot({
                "ts": time.time(),
                "market_id": btc_market.market_id,
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
                "has_active_trade": btc_market.market_id in self._bet_history,
            })
        except Exception:
            pass

    def get_bet_history(self) -> dict[str, BtcBetRecord]:
        """Return bet history for dashboard."""
        return dict(self._bet_history)

    def get_stats(self) -> dict[str, Any]:
        """Return strategy stats for monitoring."""
        total = len(self._bet_history)
        up_bets = sum(1 for b in self._bet_history.values() if b.direction == "up")
        down_bets = total - up_bets
        avg_conf = (
            sum(b.confidence for b in self._bet_history.values()) / total
            if total > 0 else 0.0
        )
        return {
            "total_bets": total,
            "up_bets": up_bets,
            "down_bets": down_bets,
            "avg_confidence": round(avg_conf, 3),
        }
