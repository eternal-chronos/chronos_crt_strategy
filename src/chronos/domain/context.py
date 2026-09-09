"""Contexto que la estrategia recibe en cada barra.

Es una clase concreta, no un puerto: solo hay una forma de mirar una barra. El
motor la construye una vez con los arrays de toda la serie y llama a `update`
antes de cada `on_bar`, así que leerla es O(1) y no puede ver el futuro: todos
los accesos se recortan en el índice actual.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.domain.bars import PRICE_COLUMNS, columns_as_arrays
from chronos.domain.enums import Side
from chronos.domain.errors import StrategyError
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.position import Position


class BarContext:
    """Vista de solo lectura de la barra en curso y del estado de la cuenta."""

    __slots__ = ("_arrays", "_balance", "_equity", "_index", "_positions", "_spec", "_timestamps")

    def __init__(self, bars: pd.DataFrame, spec: InstrumentSpec) -> None:
        self._arrays = columns_as_arrays(bars)
        self._timestamps = pd.DatetimeIndex(bars.index)
        self._spec = spec
        self._index = 0
        self._equity = 0.0
        self._balance = 0.0
        self._positions: tuple[Position, ...] = ()

    # --- Uso interno del motor ---------------------------------------------

    def update(
        self,
        *,
        index: int,
        equity: float,
        balance: float,
        positions: Sequence[Position],
    ) -> None:
        """Sitúa el contexto en la barra `index` con el estado de cuenta de ese momento."""
        self._index = index
        self._equity = equity
        self._balance = balance
        self._positions = tuple(positions)

    # --- Barra actual -------------------------------------------------------

    @property
    def index(self) -> int:
        return self._index

    @property
    def now(self) -> datetime:
        return self._timestamps[self._index].to_pydatetime()

    @property
    def open(self) -> float:
        return self.value("open")

    @property
    def high(self) -> float:
        return self.value("high")

    @property
    def low(self) -> float:
        return self.value("low")

    @property
    def close(self) -> float:
        return self.value("close")

    @property
    def volume(self) -> float:
        return self.value("volume")

    # --- Estado de la cuenta ------------------------------------------------

    @property
    def spec(self) -> InstrumentSpec:
        return self._spec

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def positions(self) -> tuple[Position, ...]:
        return self._positions

    def has_position(self, side: Side | None = None) -> bool:
        if side is None:
            return bool(self._positions)
        return any(p.side is side for p in self._positions)

    # --- Histórico ----------------------------------------------------------

    def history(self, column: str) -> np.ndarray:
        """Columna recortada hasta la barra actual (incluida). Vista, no copia."""
        return self._array(column)[: self._index + 1]

    def value(self, column: str, offset: int = 0) -> float:
        """Valor de la columna `offset` barras atrás (0 = barra actual)."""
        if offset < 0:
            raise StrategyError("No se puede leer una barra futura: offset debe ser >= 0")
        position = self._index - offset
        if position < 0:
            raise StrategyError(f"Histórico insuficiente: se pidió la barra {position}")
        return float(self._array(column)[position])

    def _array(self, column: str) -> np.ndarray:
        try:
            return self._arrays[column]
        except KeyError:
            raise StrategyError(
                f"Columna desconocida '{column}'. Disponibles: {', '.join(PRICE_COLUMNS)}"
            ) from None
