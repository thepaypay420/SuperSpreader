from __future__ import annotations

import time

from connectors.polymarket import ws_stream


def test_ws_book_event_uses_observation_time_not_payload_timestamp(monkeypatch):
    # If the payload timestamp is "stuck" (e.g. last-change time), we still want tob.ts to
    # advance with receipt time so risk doesn't falsely trip feed_lag in quiet markets.
    fake_now = 1234.5
    monkeypatch.setattr(time, "time", lambda: fake_now)

    msg = {
        "type": "market",
        "data": {
            "market_id": "m1",
            "bestBid": "0.49",
            "bestAsk": "0.51",
            "timestamp": 111.0,  # should be ignored for tob.ts
        },
    }

    ev = ws_stream._normalize_ws_message(msg)  # noqa: SLF001 - unit-test internal normalizer
    assert ev is not None
    assert ev.kind == "tob"
    assert ev.market_id == "m1"
    assert ev.tob.ts == fake_now


def test_ws_trade_event_converts_ms_epoch(monkeypatch):
    fake_now = 2000.0
    monkeypatch.setattr(time, "time", lambda: fake_now)

    msg = {
        "type": "trade",
        "data": {
            "market_id": "m1",
            "price": "0.5",
            "size": "10",
            "side": "buy",
            "timestamp": 1_700_000_000_000,  # ms epoch
        },
    }

    ev = ws_stream._normalize_ws_message(msg)  # noqa: SLF001 - unit-test internal normalizer
    assert ev is not None
    assert ev.kind == "trade"
    assert ev.trade.ts == 1_700_000_000.0

