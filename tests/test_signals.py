import asyncio
from types import SimpleNamespace

import pytest

from connectors.external_odds.base import ExternalOdds, ExternalOddsProvider
from execution.base import Broker, OrderRequest
from risk.portfolio import Portfolio
from risk.rules import RiskEngine
from storage.sqlite import SqliteStore
from strategies.base import StrategyContext
from strategies.cross_venue import CrossVenueFairValueStrategy
from trading.state import SharedState
from trading.types import MarketInfo, Order, TopOfBook


class DummyOdds(ExternalOddsProvider):
    def __init__(self, fair_prob: float):
        self._p = fair_prob

    async def get_fair_prob(self, market: MarketInfo) -> ExternalOdds:
        return ExternalOdds(fair_prob=self._p, source="dummy")


class DummyBroker(Broker):
    def __init__(self):
        self.requests: list[OrderRequest] = []

    async def place_limit(self, req: OrderRequest) -> Order:
        self.requests.append(req)
        return Order(
            order_id="oid",
            market_id=req.market_id,
            side=req.side,
            price=req.price,
            size=req.size,
            created_ts=0.0,
            status="open",
        )

    async def cancel(self, order_id: str) -> None:
        return None

    async def cancel_all_market(self, market_id: str) -> None:
        return None

    async def on_book(self, market_id: str, tob: TopOfBook):
        return []


@pytest.mark.asyncio
async def test_cross_venue_buy_signal_triggers_order(tmp_path):
    settings = SimpleNamespace(
        # strategy knobs
        edge_buffer=0.01,
        fees_bps=0.0,
        slippage_bps=0.0,
        latency_bps=0.0,
        base_order_size=10.0,
        min_trade_cooldown_secs=0.0,
        # risk
        kill_switch=False,
        max_feed_lag_secs=10.0,
        max_spread=1.0,
        max_pos_per_market=100.0,
        max_event_exposure=100000.0,
        daily_loss_limit=100000.0,
    )
    store = SqliteStore(str(tmp_path / "t.sqlite"))
    store.init_db()

    state = SharedState()
    market = MarketInfo(
        market_id="m1",
        question="q",
        event_id="e1",
        active=True,
        end_ts=None,
        volume_24h_usd=1e9,
        liquidity_usd=1e9,
    )
    async with state.lock:
        state.markets[market.market_id] = market
        state.tob[market.market_id] = TopOfBook(best_bid=0.44, best_bid_size=10, best_ask=0.45, best_ask_size=10)

    broker = DummyBroker()
    pf = Portfolio()
    risk = RiskEngine(settings)
    odds = DummyOdds(fair_prob=0.60)

    ctx = StrategyContext(settings=settings, state=state, store=store, broker=broker, risk=risk, portfolio=pf, odds=odds)
    strat = CrossVenueFairValueStrategy()
    await strat.on_market(ctx, "m1")

    assert len(broker.requests) == 1
    assert broker.requests[0].side == "buy"
    assert broker.requests[0].price == 0.45

