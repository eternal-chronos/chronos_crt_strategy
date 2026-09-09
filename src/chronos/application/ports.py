"""Puertos de la aplicación: lo único que la orquestación conoce del exterior.

Tres contratos, todos `Protocol`, todos con adaptador simulado y hueco para el
real: `Clock` (tiempo), `MarketData` (barras) y `Broker` (ejecución y estado de
la cuenta). Las implementaciones viven en `infrastructure/` y se inyectan por
constructor, a mano, desde el punto de composición (`interface/cli.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

import pandas as pd

from chronos.domain.account import Account
from chronos.domain.enums import ExitReason, Side, Timeframe
from chronos.domain.position import Position
from chronos.domain.quote import Quote
from chronos.domain.trade import Trade


class Clock(Protocol):
    """De dónde sale «ahora». El dominio nunca lo pregunta; la aplicación sí."""

    def now(self) -> datetime:
        """Instante actual, siempre tz-aware y en UTC."""
        ...


class MarketData(Protocol):
    """Fuente de barras en formato canónico (ver `domain.bars`)."""

    def bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> pd.DataFrame:
        """Barras cerradas de `symbol`, recortadas a `(since, until]` por el adaptador.

        Que el recorte por `until` ocurra aquí es lo que impide el look-ahead:
        la estrategia recibe lo que hay, no lo que habrá.
        """
        ...


class Broker(Protocol):
    """Ejecución y estado de la cuenta.

    La posición la manda el bróker: `positions` es la verdad, no una variable
    del motor. El adaptador simulado modela horquilla, comisión, swap y
    slippage; el real traducirá estas mismas llamadas a órdenes idempotentes.
    """

    # --- Estado -------------------------------------------------------------

    @property
    def account(self) -> Account: ...

    @property
    def positions(self) -> Sequence[Position]: ...

    @property
    def trades(self) -> Sequence[Trade]: ...

    def has_position(self, side: Side | None = None) -> bool: ...

    def can_afford(self, volume: float, price: float) -> bool: ...

    # --- Precios ------------------------------------------------------------

    def quote(self, price: float, spread: float | None = None) -> Quote:
        """Bid/ask ejecutables para un precio de referencia."""
        ...

    # --- Órdenes ------------------------------------------------------------

    def open_position(
        self,
        *,
        side: Side,
        volume: float,
        quote: Quote,
        timestamp: datetime,
        index: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        tag: str = "",
        comment: str = "",
    ) -> Position: ...

    def close_at_market(
        self,
        position: Position,
        *,
        quote: Quote,
        timestamp: datetime,
        index: int,
        reason: ExitReason,
    ) -> Trade: ...

    def close_all(
        self,
        *,
        quote: Quote,
        timestamp: datetime,
        index: int,
        reason: ExitReason,
        side: Side | None = None,
    ) -> Sequence[Trade]: ...

    def modify_stops(
        self,
        position: Position,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> None: ...

    # --- Avance de la barra -------------------------------------------------
    #
    # En simulación estas llamadas son el modelo de ejecución intrabar; en un
    # bróker real son consultas de reconciliación (el servidor ya ejecutó los
    # stops y devengó el swap), pero el orden de eventos del motor es el mismo.

    def apply_swap(self, weekday: int) -> float: ...

    def process_protective_orders(
        self,
        *,
        open_price: float,
        high: float,
        low: float,
        spread: float | None,
        timestamp: datetime,
        index: int,
    ) -> Sequence[Trade]: ...

    def mark_to_market(self, quote: Quote) -> None: ...

    def enforce_stop_out(
        self, *, quote: Quote, timestamp: datetime, index: int
    ) -> Sequence[Trade]: ...
