"""Precios ejecutables de un instante: bid y ask."""

from __future__ import annotations

from dataclasses import dataclass

from chronos.domain.enums import Side


@dataclass(frozen=True, slots=True)
class Quote:
    """Par bid/ask derivado del precio del dataset o del feed del bróker."""

    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    def for_entry(self, side: Side) -> float:
        return self.ask if side is Side.BUY else self.bid

    def for_exit(self, side: Side) -> float:
        return self.bid if side is Side.BUY else self.ask
