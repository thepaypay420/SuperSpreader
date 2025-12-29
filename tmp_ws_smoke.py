from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace

from connectors.polymarket.market_discovery import PolymarketMarketDiscovery
from connectors.polymarket.ws_stream import PolymarketClobWebSocketStream
from storage.sqlite import SqliteStore
from utils.logging import configure_logging


async def main() -> None:
    # Keep this script self-contained and short-lived.
    ws_url = os.getenv("POLYMARKET_WS", "wss://ws-subscriptions-clob.polymarket.com/ws/market").strip()
    seconds = float(os.getenv("WS_SMOKE_SECS", "15"))
    top_n = int(os.getenv("WS_SMOKE_TOP_N", "20"))

    # Enable logging so we can see ws.connected/ws.server_error/etc.
    configure_logging(
        SimpleNamespace(
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            json_logs=True,
            log_file=None,
            log_max_bytes=0,
            log_backup_count=0,
        )
    )

    store = SqliteStore(":memory:")
    store.init_db()
    disc = PolymarketMarketDiscovery()
    markets = await disc.fetch_markets(limit=500)

    # Pick markets that actually have CLOB token ids (required for MARKET channel).
    m2 = [m for m in markets if getattr(m, "clob_token_id", None)]
    # Prefer higher-liquidity/volume markets for more frequent updates.
    top, eligible = disc.rank_and_filter(m2, min_vol=0.0, min_liq=0.0, top_n=top_n)
    picks = top or eligible[:top_n]
    subs = [{"market_id": m.market_id, "asset_id": str(m.clob_token_id)} for m in picks if m.clob_token_id]

    print(
        {
            "ts": time.time(),
            "ws_url": ws_url,
            "picked": len(picks),
            "subs": len(subs),
            "sample": subs[:3],
        }
    )

    feed = PolymarketClobWebSocketStream(ws_url, store=store)
    start = time.time()
    n = 0
    kinds: dict[str, int] = {}
    markets_seen: set[str] = set()

    def provider():
        return subs

    async def consume() -> None:
        nonlocal n
        async for ev in feed.events(provider):
            n += 1
            kinds[ev.kind] = kinds.get(ev.kind, 0) + 1
            markets_seen.add(ev.market_id)
            if n <= 5:
                print({"ts": time.time(), "event": ev.kind, "market_id": ev.market_id})

    task = asyncio.create_task(consume())
    try:
        # Hard stop even if we never get an event.
        await asyncio.sleep(seconds)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    print(
        {
            "ts": time.time(),
            "seconds": round(time.time() - start, 2),
            "events": n,
            "kinds": kinds,
            "markets_seen": len(markets_seen),
        }
    )
    # Also show the last-known feed statuses.
    try:
        print({"runtime_status": store.fetch_runtime_statuses()})
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())

