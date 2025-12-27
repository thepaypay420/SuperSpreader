from __future__ import annotations

from dataclasses import dataclass


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def american_to_prob(american_odds: float) -> float:
    """
    Convert American odds to implied probability (no vig removal).
    +150 -> 0.4 ; -150 -> 0.6
    """
    if american_odds == 0:
        raise ValueError("american_odds cannot be 0")
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    return (-american_odds) / ((-american_odds) + 100.0)


def decimal_to_prob(decimal_odds: float) -> float:
    if decimal_odds <= 0:
        raise ValueError("decimal_odds must be > 0")
    return 1.0 / decimal_odds


def prob_to_price(prob: float) -> float:
    # Polymarket prices are in [0,1] for a binary outcome
    return clamp(prob, 0.0, 1.0)


def price_to_prob(price: float) -> float:
    return clamp(price, 0.0, 1.0)


def bps_to_decimal(bps: float) -> float:
    return bps / 10000.0


@dataclass(frozen=True)
class FairValue:
    fair_prob: float
    fair_price: float


def apply_buffers(price: float, fees_bps: float, slippage_bps: float, latency_bps: float, side: str) -> float:
    """
    Returns a conservative fair price after buffers.
    For buys: reduce fair price (harder to justify buying).
    For sells: increase fair price (harder to justify selling).
    """
    buf = bps_to_decimal(fees_bps + slippage_bps + latency_bps)
    if side.lower() == "buy":
        return clamp(price - buf, 0.0, 1.0)
    if side.lower() == "sell":
        return clamp(price + buf, 0.0, 1.0)
    raise ValueError("side must be buy|sell")
