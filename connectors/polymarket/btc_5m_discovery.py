"""
BTC Up/Down 5-Minute Market Discovery.

Discovers the next active "Bitcoin Up or Down" 5-minute market from
Polymarket's recurring series (series ticker: btc-up-or-down-5m).

Each market in this series:
- Has outcomes: ["Up", "Down"]
- Resolves based on Chainlink BTC/USD price direction over a 5-minute window
- eventStartTime = when the 5m window opens
- endDate = when the 5m window closes (= eventStartTime + 5 minutes)
- Trades around 0.50 (coin-flip base), shifts with BTC momentum
- New markets spin up every 5 minutes, 24/7

Discovery strategy:
1. Query the Gamma events API for the series
2. Find the next market that is:
   a. active and not closed
   b. acceptingOrders = true
   c. eventStartTime is in the near future (or right now)
3. Return it so the strategy can place a directional bet before the window opens
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from trading.types import MarketInfo
from utils.logging import get_logger

_log = get_logger(__name__)

# Series slug for the recurring BTC 5m up/down markets
BTC_5M_SERIES_SLUG = "btc-up-or-down-5m"


@dataclass(frozen=True)
class BtcUpDownMarket:
    """Enriched market info for a BTC 5m up/down event."""
    market_id: str
    event_slug: str
    question: str
    event_id: str
    condition_id: str | None
    # Token IDs: index 0 = "Up", index 1 = "Down"
    up_token_id: str | None
    down_token_id: str | None
    # Timing
    event_start_ts: float | None   # when the 5m window opens
    event_end_ts: float | None     # when the 5m window closes
    # Current book
    best_bid: float | None
    best_ask: float | None
    # State
    accepting_orders: bool
    active: bool
    closed: bool
    volume: float

    def to_market_info(self) -> MarketInfo:
        """Convert to the standard MarketInfo type used by the rest of the system."""
        return MarketInfo(
            market_id=self.market_id,
            question=self.question,
            event_id=self.event_id,
            active=self.active,
            end_ts=self.event_end_ts,
            volume_24h_usd=self.volume,
            liquidity_usd=0.0,
            condition_id=self.condition_id,
            clob_token_id=self.up_token_id,  # "Up" token is our primary
        )


def _parse_iso_ts(s: str | None) -> float | None:
    """Best-effort ISO 8601 -> epoch seconds. No external deps."""
    if not s:
        return None
    try:
        from datetime import datetime, timezone
        # Handle common Polymarket formats: "2026-02-15T05:30:00Z" or with offset
        s = s.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt.timestamp()
    except Exception:
        return None


def _parse_token_ids(raw: Any) -> list[str]:
    """Parse clobTokenIds from either a JSON string or a list."""
    if isinstance(raw, list):
        return [str(x).strip().strip("'\"") for x in raw if str(x).strip()]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip().strip("'\"") for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [x.strip().strip("'\"") for x in raw.strip("[]").split(",") if x.strip()]
    return []


class BtcFiveMinDiscovery:
    """
    Discovers BTC 5-minute up/down markets from the Polymarket series.

    Methods:
    - fetch_upcoming(): Find the next/current active BTC 5m market
    - fetch_recent_settled(): Get recently settled markets for backtesting signals
    """

    def __init__(self, base_url: str = "https://gamma-api.polymarket.com"):
        self._base = base_url.rstrip("/")
        self._log = get_logger(__name__)
        self._cached: BtcUpDownMarket | None = None
        self._cached_ts: float = 0.0

    def _parse_event_to_market(self, ev: dict[str, Any]) -> BtcUpDownMarket | None:
        """Parse a Gamma API event response into a BtcUpDownMarket."""
        markets = ev.get("markets", [])
        if not markets:
            return None
        m = markets[0]  # each BTC 5m event has exactly one market

        market_id = str(m.get("id") or "")
        if not market_id:
            return None

        token_ids = _parse_token_ids(m.get("clobTokenIds"))
        outcomes = m.get("outcomes", "")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []

        # Map tokens to Up/Down based on outcomes array
        up_token = token_ids[0] if len(token_ids) > 0 else None
        down_token = token_ids[1] if len(token_ids) > 1 else None

        # If outcomes are explicitly labeled, use that mapping
        if isinstance(outcomes, list):
            for i, o in enumerate(outcomes):
                label = str(o).strip().lower()
                if label == "up" and i < len(token_ids):
                    up_token = token_ids[i]
                elif label == "down" and i < len(token_ids):
                    down_token = token_ids[i]

        return BtcUpDownMarket(
            market_id=market_id,
            event_slug=str(ev.get("slug", "")),
            question=str(m.get("question") or ev.get("title", "")),
            event_id=str(ev.get("id", f"event:{market_id}")),
            condition_id=str(m.get("conditionId") or "") or None,
            up_token_id=up_token,
            down_token_id=down_token,
            event_start_ts=_parse_iso_ts(m.get("eventStartTime")),
            event_end_ts=_parse_iso_ts(m.get("endDate")),
            best_bid=float(m["bestBid"]) if m.get("bestBid") is not None else None,
            best_ask=float(m["bestAsk"]) if m.get("bestAsk") is not None else None,
            accepting_orders=bool(m.get("acceptingOrders", False)),
            active=bool(m.get("active", False)),
            closed=bool(m.get("closed", False)),
            volume=float(m.get("volume") or m.get("volumeNum") or 0.0),
        )

    async def fetch_upcoming(self, look_ahead_secs: float = 600.0) -> BtcUpDownMarket | None:
        """
        Find the next/current active BTC 5m up/down market.

        Returns the best market to trade right now, or None if nothing is available.
        Prefers markets that are:
        1. acceptingOrders = true
        2. Not yet closed
        3. eventStartTime is soonest in the future
        """
        now = time.time()

        # Cache for 10 seconds to avoid hammering the API
        if self._cached and (now - self._cached_ts) < 10.0:
            if self._cached.accepting_orders and not self._cached.closed:
                return self._cached

        url = f"{self._base}/events"
        params = {
            "slug_contains": "btc-updown-5m",
            "active": "true",
            "closed": "false",
            "limit": "20",
        }
        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            if not isinstance(data, list):
                self._log.warning("btc_5m_discovery.bad_response", type=str(type(data)))
                return None

            candidates: list[BtcUpDownMarket] = []
            for ev in data:
                if not isinstance(ev, dict):
                    continue
                slug = str(ev.get("slug", ""))
                if "btc-updown-5m" not in slug:
                    continue
                m = self._parse_event_to_market(ev)
                if m is None:
                    continue
                if not m.accepting_orders or m.closed:
                    continue
                candidates.append(m)

            if not candidates:
                self._log.info("btc_5m_discovery.no_active_markets")
                self._cached = None
                return None

            # Sort: prefer markets whose window hasn't started yet (eventStartTime > now)
            # If all have started, prefer the one closing soonest
            def sort_key(m: BtcUpDownMarket):
                start = m.event_start_ts or 0.0
                end = m.event_end_ts or 0.0
                # Prefer: upcoming (not yet started) > currently open > others
                if start > now:
                    return (0, start)  # upcoming, soonest first
                elif end > now:
                    return (1, end)    # currently open
                else:
                    return (2, end)    # about to close

            candidates.sort(key=sort_key)
            best = candidates[0]

            self._cached = best
            self._cached_ts = now
            self._log.info(
                "btc_5m_discovery.found",
                market_id=best.market_id,
                slug=best.event_slug,
                event_start=best.event_start_ts,
                event_end=best.event_end_ts,
                best_bid=best.best_bid,
                best_ask=best.best_ask,
                accepting=best.accepting_orders,
            )
            return best

        except Exception as e:
            self._log.exception("btc_5m_discovery.error")
            return self._cached  # return stale cache on error

    async def fetch_recent_settled(self, limit: int = 50) -> list[BtcUpDownMarket]:
        """
        Fetch recently settled BTC 5m markets (for building signal history).
        Returns most recent first.
        """
        url = f"{self._base}/events"
        params = {
            "slug_contains": "btc-updown-5m",
            "closed": "true",
            "limit": str(limit),
        }
        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            if not isinstance(data, list):
                return []

            results: list[BtcUpDownMarket] = []
            for ev in data:
                if not isinstance(ev, dict):
                    continue
                slug = str(ev.get("slug", ""))
                if "btc-updown-5m" not in slug:
                    continue
                m = self._parse_event_to_market(ev)
                if m is not None:
                    results.append(m)

            # Sort by event_end_ts descending (most recent first)
            results.sort(key=lambda m: m.event_end_ts or 0.0, reverse=True)
            return results

        except Exception:
            self._log.exception("btc_5m_discovery.settled_error")
            return []
