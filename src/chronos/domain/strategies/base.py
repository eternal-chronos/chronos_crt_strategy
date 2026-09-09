"""Base cómoda para estrategias basadas en indicadores.

Resuelve el patrón repetido: precalcular indicadores vectorizados una vez y
leerlos por índice dentro de `on_bar`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from chronos.domain.bars import columns_as_arrays
from chronos.domain.errors import StrategyError
from chronos.domain.strategy import Strategy


class IndicatorStrategy(Strategy):
    """Estrategia que declara sus indicadores en `compute_indicators`."""

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self._indicators: dict[str, np.ndarray] = {}

    # --- A implementar por la estrategia concreta ---------------------------

    def compute_indicators(
        self,
        *,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """Devuelve los indicadores alineados con la serie completa."""
        return {}

    # --- Infraestructura de la base ----------------------------------------

    def prepare(self, bars: pd.DataFrame) -> None:
        arrays = columns_as_arrays(bars)
        self._indicators = self.compute_indicators(
            open_=arrays["open"],
            high=arrays["high"],
            low=arrays["low"],
            close=arrays["close"],
            volume=arrays["volume"],
        )
        expected = len(bars)
        for name, series in self._indicators.items():
            if len(series) != expected:
                raise StrategyError(
                    f"El indicador '{name}' tiene {len(series)} valores y la serie {expected}"
                )

    def ind(self, name: str, index: int, offset: int = 0) -> float:
        """Valor del indicador `offset` barras antes de `index`."""
        if offset < 0:
            raise StrategyError("offset negativo: sería mirar al futuro")
        try:
            series = self._indicators[name]
        except KeyError:
            available = ", ".join(sorted(self._indicators)) or "ninguno"
            raise StrategyError(
                f"Indicador desconocido '{name}'. Calculados: {available}"
            ) from None
        position = index - offset
        if position < 0:
            raise StrategyError("Histórico insuficiente para leer el indicador")
        return float(series[position])

    def series(self, name: str) -> np.ndarray:
        """Serie completa del indicador. Recórtala antes de usarla en `on_bar`."""
        try:
            return self._indicators[name]
        except KeyError:
            raise StrategyError(f"Indicador desconocido '{name}'") from None

    @staticmethod
    def is_ready(*values: float) -> bool:
        """`True` si ningún indicador está todavía en calentamiento."""
        return not any(np.isnan(value) for value in values)
