"""Tests for BTC 5-minute up/down market discovery."""
from __future__ import annotations

import json

import pytest

from connectors.polymarket.btc_5m_discovery import (
    BtcFiveMinDiscovery,
    BtcUpDownMarket,
    _parse_iso_ts,
    _parse_token_ids,
)


def test_parse_iso_ts_z():
    ts = _parse_iso_ts("2026-02-15T05:30:00Z")
    assert ts is not None
    assert isinstance(ts, float)
    assert ts > 0


def test_parse_iso_ts_offset():
    ts = _parse_iso_ts("2026-02-15T05:30:00+00:00")
    assert ts is not None


def test_parse_iso_ts_none():
    assert _parse_iso_ts(None) is None
    assert _parse_iso_ts("") is None


def test_parse_token_ids_list():
    tokens = _parse_token_ids(["abc123", "def456"])
    assert tokens == ["abc123", "def456"]


def test_parse_token_ids_json_string():
    s = '["abc123", "def456"]'
    tokens = _parse_token_ids(s)
    assert tokens == ["abc123", "def456"]


def test_parse_token_ids_empty():
    assert _parse_token_ids([]) == []
    assert _parse_token_ids("") == []
    assert _parse_token_ids(None) == []


def test_btc_updown_market_dataclass():
    m = BtcUpDownMarket(
        market_id="12345",
        event_slug="btc-updown-5m-test",
        question="Bitcoin Up or Down - Test",
        event_id="evt-12345",
        condition_id="0xabc",
        up_token_id="tok-up",
        down_token_id="tok-down",
        event_start_ts=1771133400.0,
        event_end_ts=1771133700.0,
        best_bid=0.49,
        best_ask=0.51,
        accepting_orders=True,
        active=True,
        closed=False,
        volume=5000.0,
    )
    assert m.market_id == "12345"
    assert m.up_token_id == "tok-up"
    assert m.down_token_id == "tok-down"


def test_btc_updown_to_market_info():
    m = BtcUpDownMarket(
        market_id="12345",
        event_slug="btc-updown-5m-test",
        question="Bitcoin Up or Down - Test",
        event_id="evt-12345",
        condition_id="0xabc",
        up_token_id="tok-up",
        down_token_id="tok-down",
        event_start_ts=1771133400.0,
        event_end_ts=1771133700.0,
        best_bid=0.49,
        best_ask=0.51,
        accepting_orders=True,
        active=True,
        closed=False,
        volume=5000.0,
    )
    info = m.to_market_info()
    assert info.market_id == "12345"
    assert info.question == "Bitcoin Up or Down - Test"
    assert info.event_id == "evt-12345"
    assert info.end_ts == 1771133700.0
    assert info.clob_token_id == "tok-up"
    assert info.active is True


def test_parse_event_to_market():
    """Test parsing a real-world-like Gamma API event response."""
    d = BtcFiveMinDiscovery()
    ev = {
        "id": "207213",
        "slug": "btc-updown-5m-1771133400",
        "title": "Bitcoin Up or Down - February 15, 12:30AM-12:35AM ET",
        "markets": [{
            "id": "1375833",
            "question": "Bitcoin Up or Down - February 15, 12:30AM-12:35AM ET",
            "conditionId": "0xabc123",
            "outcomes": '["Up", "Down"]',
            "clobTokenIds": '["tok-up-111", "tok-down-222"]',
            "eventStartTime": "2026-02-15T05:30:00Z",
            "endDate": "2026-02-15T05:35:00Z",
            "bestBid": 0.49,
            "bestAsk": 0.51,
            "acceptingOrders": True,
            "active": True,
            "closed": False,
            "volume": "5000.0",
        }],
    }
    m = d._parse_event_to_market(ev)
    assert m is not None
    assert m.market_id == "1375833"
    assert m.event_slug == "btc-updown-5m-1771133400"
    assert m.up_token_id == "tok-up-111"
    assert m.down_token_id == "tok-down-222"
    assert m.accepting_orders is True
    assert m.event_start_ts is not None
    assert m.event_end_ts is not None
    assert m.best_bid == 0.49
    assert m.best_ask == 0.51
