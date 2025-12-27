from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict

from execution.base import Broker, OrderRequest
from storage.sqlite import SqliteStore
from trading.types import Fill, Order, TopOfBook
from utils.logging import get_logger


class PaperBroker(Broker):
    """
    Paper trading execution:
    - Keeps an in-memory order blotter.
    - Simulates immediate fills when an order crosses the spread at the time of placement,
      otherwise fills when subsequent book updates cross the order price.
    """

    def __init__(self, store: SqliteStore):
        self._store = store
        self._orders: dict[str, Order] = {}
        self._by_market: dict[str, set[str]] = {}
        self._meta_by_order_id: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._log = get_logger(__name__)

    async def place_limit(self, req: OrderRequest) -> Order:
        oid = str(uuid.uuid4())
        now = time.time()
        order = Order(
            order_id=oid,
            market_id=req.market_id,
            side=req.side,
            price=float(req.price),
            size=float(req.size),
            created_ts=now,
            status="open",
        )
        async with self._lock:
            self._orders[oid] = order
            self._by_market.setdefault(req.market_id, set()).add(oid)
            self._meta_by_order_id[oid] = dict(req.meta or {})
        self._store.insert_order({**asdict(order), "meta": req.meta or {}})
        self._log.info(
            "order.placed.paper",
            order_id=oid,
            market_id=req.market_id,
            side=req.side,
            price=req.price,
            size=req.size,
            meta=req.meta or {},
        )
        return order

    async def cancel(self, order_id: str) -> None:
        async with self._lock:
            o = self._orders.get(order_id)
            if not o or o.status != "open":
                return
            o.status = "cancelled"
        self._store.update_order_status(order_id, "cancelled")
        self._log.info("order.cancelled.paper", order_id=order_id)

    async def cancel_all_market(self, market_id: str) -> None:
        async with self._lock:
            oids = list(self._by_market.get(market_id, set()))
        for oid in oids:
            await self.cancel(oid)

    async def on_book(self, market_id: str, tob: TopOfBook) -> list[Fill]:
        fills: list[Fill] = []
        async with self._lock:
            oids = list(self._by_market.get(market_id, set()))
            for oid in oids:
                o = self._orders.get(oid)
                if not o or o.status != "open":
                    continue
                fill_price = None
                if o.side == "buy" and tob.best_ask is not None and o.price >= tob.best_ask:
                    fill_price = tob.best_ask
                if o.side == "sell" and tob.best_bid is not None and o.price <= tob.best_bid:
                    fill_price = tob.best_bid
                if fill_price is None:
                    continue

                meta = dict(self._meta_by_order_id.get(o.order_id, {}))
                meta.update(
                    {
                        "fill_model": "on_book_cross",
                        "tob_best_bid": tob.best_bid,
                        "tob_best_ask": tob.best_ask,
                        "tob_ts": tob.ts,
                    }
                )
                f = Fill(
                    fill_id=str(uuid.uuid4()),
                    order_id=o.order_id,
                    market_id=o.market_id,
                    side=o.side,
                    price=float(fill_price),
                    size=float(o.size - o.filled_size),
                    ts=time.time(),
                    meta=meta,
                )
                o.filled_size = o.size
                o.status = "filled"
                fills.append(f)
                self._store.update_order_status(o.order_id, "filled", filled_size=o.filled_size)
                self._store.insert_fill(asdict(f))

        for f in fills:
            self._log.info(
                "fill.paper",
                fill_id=f.fill_id,
                order_id=f.order_id,
                market_id=f.market_id,
                side=f.side,
                price=f.price,
                size=f.size,
                meta=f.meta,
            )
        return fills

