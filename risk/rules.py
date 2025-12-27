from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from trading.types import Side, TopOfBook
from utils.pricing import clamp


@dataclass(frozen=True)
class RiskCheck:
    ok: bool
    reason: str | None = None


def _mid(tob: TopOfBook) -> float | None:
    if tob.best_bid is not None and tob.best_ask is not None:
        return 0.5 * (tob.best_bid + tob.best_ask)
    if tob.best_bid is not None:
        return tob.best_bid
    if tob.best_ask is not None:
        return tob.best_ask
    return None


class RiskEngine:
    def __init__(self, settings: Any):
        self.settings = settings

    def circuit_ok(self, tob: TopOfBook | None) -> RiskCheck:
        if tob is None:
            return RiskCheck(False, "no_top_of_book")
        now = time.time()
        if (now - tob.ts) > float(self.settings.max_feed_lag_secs):
            return RiskCheck(False, "feed_lag")
        if tob.best_bid is not None and tob.best_ask is not None:
            spread = tob.best_ask - tob.best_bid
            if spread < 0:
                return RiskCheck(False, "crossed_book")
            if spread > float(self.settings.max_spread):
                return RiskCheck(False, "spread_too_wide")
        return RiskCheck(True)

    def pre_trade_check(
        self,
        *,
        market_id: str,
        event_id: str,
        side: Side,
        price: float,
        size: float,
        tob: TopOfBook | None,
        portfolio: Any,
    ) -> RiskCheck:
        if bool(self.settings.kill_switch):
            return RiskCheck(False, "kill_switch")
        if size <= 0:
            return RiskCheck(False, "bad_size")
        if not (0.0 <= price <= 1.0):
            return RiskCheck(False, "bad_price")

        c = self.circuit_ok(tob)
        if not c.ok:
            return c

        pos = portfolio.positions.get(market_id)
        cur_qty = 0.0 if pos is None else float(pos.qty)
        signed = size if side == "buy" else -size
        new_qty = cur_qty + signed
        if abs(new_qty) > float(self.settings.max_pos_per_market):
            return RiskCheck(False, "max_pos_per_market")

        # Event exposure: sum abs(qty)*mid across markets in same event
        event_exposure = 0.0
        for p in portfolio.positions.values():
            if p.event_id != event_id:
                continue
            mark = p.last_mark if p.last_mark > 0 else p.avg_price
            event_exposure += abs(float(p.qty)) * float(clamp(mark, 0.0, 1.0))
        # Add prospective order at its limit price
        event_exposure += abs(signed) * float(clamp(price, 0.0, 1.0))
        if event_exposure > float(self.settings.max_event_exposure):
            return RiskCheck(False, "max_event_exposure")

        # Daily loss limit: realized + marked unrealized
        unreal = 0.0
        for mid_market_id, p in portfolio.positions.items():
            _ = mid_market_id
            tob_mid = None
            # portfolio keeps last_mark; prefer it
            if p.last_mark > 0:
                tob_mid = p.last_mark
            else:
                tob_mid = p.avg_price
            unreal += (tob_mid - p.avg_price) * p.qty
        total_pnl = float(portfolio.total_realized()) + float(unreal)
        if total_pnl < -float(self.settings.daily_loss_limit):
            return RiskCheck(False, "daily_loss_limit")

        return RiskCheck(True)

