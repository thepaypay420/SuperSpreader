"""
Technical indicators for 5-minute chart trading.

All functions operate on plain lists of floats (close prices, volumes, etc.)
so they are easy to test and compose. No external dependencies.

Proven indicators implemented:
- EMA (Exponential Moving Average)
- SMA (Simple Moving Average)
- RSI (Relative Strength Index) - Wilder's smoothing
- VWAP (Volume-Weighted Average Price) - session-based
- Bollinger Bands
- ATR (Average True Range) - for stop-loss sizing
- MACD (Moving Average Convergence Divergence)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


def sma(values: Sequence[float], period: int) -> list[float]:
    """
    Simple Moving Average.
    Returns a list the same length as `values`; elements before `period` are NaN.
    """
    n = len(values)
    out: list[float] = [float("nan")] * n
    if period <= 0 or n < period:
        return out
    s = sum(values[:period])
    out[period - 1] = s / period
    for i in range(period, n):
        s += values[i] - values[i - period]
        out[i] = s / period
    return out


def ema(values: Sequence[float], period: int) -> list[float]:
    """
    Exponential Moving Average.
    Seed with SMA of first `period` values, then apply EMA smoothing.
    Returns a list the same length as `values`; elements before `period` are NaN.
    """
    n = len(values)
    out: list[float] = [float("nan")] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1.0)
    # Seed: SMA of first `period` elements
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, n):
        out[i] = values[i] * k + out[i - 1] * (1.0 - k)
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[float]:
    """
    Relative Strength Index using Wilder's smoothing method.
    Returns list same length as `closes`; first `period` elements are NaN.
    """
    n = len(closes)
    out: list[float] = [float("nan")] * n
    if period <= 0 or n < period + 1:
        return out

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))

    # Wilder's smoothing: seed with SMA, then exponential
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return out


def vwap_session(prices: Sequence[float], volumes: Sequence[float]) -> list[float]:
    """
    Session VWAP (cumulative from start of the price/volume arrays).
    Returns list same length as `prices`.
    """
    n = len(prices)
    out: list[float] = [float("nan")] * n
    if n == 0:
        return out
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(n):
        v = volumes[i] if i < len(volumes) else 0.0
        if v > 0:
            cum_pv += prices[i] * v
            cum_v += v
        if cum_v > 0:
            out[i] = cum_pv / cum_v
        elif i > 0 and not math.isnan(out[i - 1]):
            out[i] = out[i - 1]
    return out


@dataclass(frozen=True)
class BollingerBands:
    upper: list[float]
    middle: list[float]  # SMA
    lower: list[float]


def bollinger_bands(
    closes: Sequence[float], period: int = 20, num_std: float = 2.0
) -> BollingerBands:
    """
    Bollinger Bands: SMA +/- num_std * stddev.
    """
    n = len(closes)
    mid = sma(closes, period)
    upper: list[float] = [float("nan")] * n
    lower: list[float] = [float("nan")] * n

    for i in range(period - 1, n):
        m = mid[i]
        if math.isnan(m):
            continue
        window = closes[i - period + 1 : i + 1]
        variance = sum((x - m) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = m + num_std * std
        lower[i] = m - num_std * std

    return BollingerBands(upper=upper, middle=mid, lower=lower)


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float]:
    """
    Average True Range (Wilder's smoothing).
    Used for position sizing and stop-loss placement.
    """
    n = len(closes)
    out: list[float] = [float("nan")] * n
    if period <= 0 or n < period + 1:
        return out

    trs: list[float] = [0.0]
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        c_prev = closes[i - 1]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)

    # Seed with SMA
    avg_tr = sum(trs[1 : period + 1]) / period
    out[period] = avg_tr

    for i in range(period + 1, n):
        avg_tr = (avg_tr * (period - 1) + trs[i]) / period
        out[i] = avg_tr

    return out


@dataclass(frozen=True)
class MACDResult:
    macd_line: list[float]      # fast EMA - slow EMA
    signal_line: list[float]    # EMA of MACD line
    histogram: list[float]      # MACD line - signal line


def macd(
    closes: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    """
    MACD (Moving Average Convergence Divergence).
    Classic: 12/26/9 periods.
    """
    n = len(closes)
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)

    macd_line: list[float] = [float("nan")] * n
    for i in range(n):
        if math.isnan(fast[i]) or math.isnan(slow[i]):
            continue
        macd_line[i] = fast[i] - slow[i]

    # Signal line = EMA of MACD line (skip NaNs for seeding)
    valid_macd = [v for v in macd_line if not math.isnan(v)]
    signal_raw = ema(valid_macd, signal_period) if len(valid_macd) >= signal_period else [float("nan")] * len(valid_macd)

    signal_line: list[float] = [float("nan")] * n
    histogram: list[float] = [float("nan")] * n

    vi = 0
    for i in range(n):
        if math.isnan(macd_line[i]):
            continue
        if vi < len(signal_raw):
            signal_line[i] = signal_raw[vi]
        vi += 1
        if not math.isnan(signal_line[i]) and not math.isnan(macd_line[i]):
            histogram[i] = macd_line[i] - signal_line[i]

    return MACDResult(macd_line=macd_line, signal_line=signal_line, histogram=histogram)


# --- Convenience: extract indicator values from candle lists ---

def closes_from_candles(candles) -> list[float]:
    """Extract close prices from a list of Candle objects."""
    return [float(c.close) for c in candles]


def highs_from_candles(candles) -> list[float]:
    return [float(c.high) for c in candles]


def lows_from_candles(candles) -> list[float]:
    return [float(c.low) for c in candles]


def volumes_from_candles(candles) -> list[float]:
    return [float(c.volume) for c in candles]


@dataclass(frozen=True)
class SignalSnapshot:
    """
    A snapshot of all indicator values at the latest candle.
    Used by the strategy to make decisions.
    """
    ts: float
    market_id: str
    close: float

    ema_fast: float | None = None          # e.g. EMA(9)
    ema_slow: float | None = None          # e.g. EMA(21)
    ema_trend: float | None = None         # e.g. EMA(50) for trend filter

    rsi_value: float | None = None         # RSI(14)

    vwap_value: float | None = None        # Session VWAP

    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None

    atr_value: float | None = None         # ATR(14)

    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None

    # Derived signals
    ema_cross_up: bool = False             # fast EMA just crossed above slow EMA
    ema_cross_down: bool = False           # fast EMA just crossed below slow EMA
    above_vwap: bool = False               # close > VWAP
    rsi_oversold: bool = False             # RSI < oversold threshold
    rsi_overbought: bool = False           # RSI > overbought threshold


def _safe_last(values: list[float]) -> float | None:
    """Return last non-NaN value or None."""
    for v in reversed(values):
        if not math.isnan(v):
            return v
    return None


def _safe_idx(values: list[float], idx: int) -> float | None:
    """Return value at index if valid, else None."""
    if 0 <= idx < len(values) and not math.isnan(values[idx]):
        return values[idx]
    return None


def compute_signals(
    candles,
    market_id: str,
    *,
    fast_ema_period: int = 9,
    slow_ema_period: int = 21,
    trend_ema_period: int = 50,
    rsi_period: int = 14,
    bb_period: int = 20,
    bb_std: float = 2.0,
    atr_period: int = 14,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 70.0,
) -> SignalSnapshot | None:
    """
    Compute all technical indicator values from a list of candles.
    Returns a SignalSnapshot for the latest candle, or None if insufficient data.
    """
    if len(candles) < max(slow_ema_period, trend_ema_period, bb_period, atr_period, rsi_period) + 1:
        return None

    c = closes_from_candles(candles)
    h = highs_from_candles(candles)
    l = lows_from_candles(candles)
    v = volumes_from_candles(candles)

    n = len(c)
    last_idx = n - 1
    prev_idx = n - 2

    ema_f = ema(c, fast_ema_period)
    ema_s = ema(c, slow_ema_period)
    ema_t = ema(c, trend_ema_period)
    rsi_vals = rsi(c, rsi_period)
    vwap_vals = vwap_session(c, v)
    bb = bollinger_bands(c, bb_period, bb_std)
    atr_vals = atr(h, l, c, atr_period)

    ema_fast_now = _safe_idx(ema_f, last_idx)
    ema_slow_now = _safe_idx(ema_s, last_idx)
    ema_fast_prev = _safe_idx(ema_f, prev_idx)
    ema_slow_prev = _safe_idx(ema_s, prev_idx)

    # EMA crossover detection
    cross_up = False
    cross_down = False
    if all(v is not None for v in [ema_fast_now, ema_slow_now, ema_fast_prev, ema_slow_prev]):
        cross_up = (ema_fast_prev <= ema_slow_prev) and (ema_fast_now > ema_slow_now)  # type: ignore[operator]
        cross_down = (ema_fast_prev >= ema_slow_prev) and (ema_fast_now < ema_slow_now)  # type: ignore[operator]

    rsi_now = _safe_idx(rsi_vals, last_idx)
    vwap_now = _safe_idx(vwap_vals, last_idx)

    return SignalSnapshot(
        ts=candles[-1].close_ts if hasattr(candles[-1], "close_ts") else 0.0,
        market_id=market_id,
        close=c[-1],
        ema_fast=ema_fast_now,
        ema_slow=ema_slow_now,
        ema_trend=_safe_idx(ema_t, last_idx),
        rsi_value=rsi_now,
        vwap_value=vwap_now,
        bb_upper=_safe_idx(bb.upper, last_idx),
        bb_middle=_safe_idx(bb.middle, last_idx),
        bb_lower=_safe_idx(bb.lower, last_idx),
        atr_value=_safe_idx(atr_vals, last_idx),
        macd_line=None,  # computed on demand
        macd_signal=None,
        macd_histogram=None,
        ema_cross_up=cross_up,
        ema_cross_down=cross_down,
        above_vwap=(c[-1] > vwap_now) if vwap_now is not None else False,
        rsi_oversold=(rsi_now is not None and rsi_now < rsi_oversold),
        rsi_overbought=(rsi_now is not None and rsi_now > rsi_overbought),
    )
