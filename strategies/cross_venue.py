from __future__ import annotations

import time

from execution.base import OrderRequest
from strategies.base import Strategy, StrategyContext
from utils.logging import get_logger
from utils.pricing import apply_buffers, prob_to_price


class CrossVenueFairValueStrategy(Strategy):
    name = "cross_venue_fv"

    def __init__(self):
        self._log = get_logger(__name__)
        self._last_trade_ts: dict[str, float] = {}

    async def on_market(self, ctx: StrategyContext, market_id: str) -> None:
        async with ctx.state.lock:
            m = ctx.state.markets.get(market_id)
            tob = ctx.state.tob.get(market_id)
        if m is None or tob is None:
            return
        if tob.best_bid is None or tob.best_ask is None:
            return

        now = time.time()
        if (now - self._last_trade_ts.get(market_id, 0.0)) < float(ctx.settings.min_trade_cooldown_secs):
            return

        ext = await ctx.odds.get_fair_prob(m)
        fair_price = prob_to_price(ext.fair_prob)

        # Conservative fair after buffers
        buy_fair = apply_buffers(fair_price, ctx.settings.fees_bps, ctx.settings.slippage_bps, ctx.settings.latency_bps, "buy")
        sell_fair = apply_buffers(fair_price, ctx.settings.fees_bps, ctx.settings.slippage_bps, ctx.settings.latency_bps, "sell")
        edge = float(ctx.settings.edge_buffer)

        # Buy if ask is sufficiently cheap vs external FV
        if tob.best_ask < (buy_fair - edge):
            px = tob.best_ask
            size = float(ctx.settings.base_order_size)
            r = ctx.risk.pre_trade_check(
                market_id=market_id,
                event_id=m.event_id,
                side="buy",
                price=px,
                size=size,
                tob=tob,
                portfolio=ctx.portfolio,
            )
            if r.ok:
                await ctx.broker.place_limit(
                    OrderRequest(
                        market_id=market_id,
                        side="buy",
                        price=px,
                        size=size,
                        meta={"strategy": self.name, "fair_price": fair_price, "source": ext.source},
                    )
                )
                self._last_trade_ts[market_id] = now
                self._log.info(
                    "signal.cross_venue.buy",
                    market_id=market_id,
                    best_ask=tob.best_ask,
                    fair_price=fair_price,
                    buy_fair=buy_fair,
                    edge=edge,
                )
            return

        # Sell if bid is sufficiently rich vs external FV
        if tob.best_bid > (sell_fair + edge):
            px = tob.best_bid
            size = float(ctx.settings.base_order_size)
            r = ctx.risk.pre_trade_check(
                market_id=market_id,
                event_id=m.event_id,
                side="sell",
                price=px,
                size=size,
                tob=tob,
                portfolio=ctx.portfolio,
            )
            if r.ok:
                await ctx.broker.place_limit(
                    OrderRequest(
                        market_id=market_id,
                        side="sell",
                        price=px,
                        size=size,
                        meta={"strategy": self.name, "fair_price": fair_price, "source": ext.source},
                    )
                )
                self._last_trade_ts[market_id] = now
                self._log.info(
                    "signal.cross_venue.sell",
                    market_id=market_id,
                    best_bid=tob.best_bid,
                    fair_price=fair_price,
                    sell_fair=sell_fair,
                    edge=edge,
                )
            return

