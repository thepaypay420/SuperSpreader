from __future__ import annotations

from execution.base import Broker, OrderRequest
from trading.types import Fill, Order, TopOfBook


class LiveBroker(Broker):
    """
    Live broker wrapper around Polymarket's official `py-clob-client`.
    Disabled by default (TRADE_MODE=paper). This implementation is intentionally conservative:
    you must provide credentials and explicitly enable TRADE_MODE=live.
    """

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "LiveBroker is disabled in this scaffold. "
            "Set TRADE_MODE=paper for end-to-end operation. "
            "Then implement/enable LiveBroker with py-clob-client for real orders."
        )

    async def place_limit(self, req: OrderRequest) -> Order:  # pragma: no cover
        raise NotImplementedError

    async def cancel(self, order_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def cancel_all_market(self, market_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def on_book(self, market_id: str, tob: TopOfBook) -> list[Fill]:  # pragma: no cover
        return []

