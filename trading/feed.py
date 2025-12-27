from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from trading.types import TopOfBook, TradeTick


@dataclass(frozen=True)
class BookEvent:
    kind: Literal["tob"]
    market_id: str
    tob: TopOfBook


@dataclass(frozen=True)
class TradeEvent:
    kind: Literal["trade"]
    market_id: str
    trade: TradeTick


FeedEvent = Union[BookEvent, TradeEvent]

