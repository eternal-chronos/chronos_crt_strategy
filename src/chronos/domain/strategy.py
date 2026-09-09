"""Contrato que toda estrategia debe cumplir.

El motor depende de esta clase base, no de ninguna estrategia concreta. Las
estrategias viven en `chronos.domain.strategies`: son funciones puras sobre
arrays, sin red, sin ficheros y sin reloj.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

import pandas as pd

from chronos.domain.context import BarContext
from chronos.domain.signal import StrategyAction
from chronos.domain.trade import Trade


class Strategy(ABC):
    """Estrategia dirigida por barras.

    Ciclo de vida por corrida:
        prepare(bars)  -> una vez, para precalcular indicadores vectorizados
        on_bar(ctx)    -> una vez por barra, devuelve intenciones
        on_trade_closed(trade) -> tras cada cierre
        on_finish()    -> al terminar
    """

    name: ClassVar[str] = "unnamed"

    def __init__(self, **params: Any) -> None:
        self.params: Mapping[str, Any] = dict(params)

    # --- Ciclo de vida ------------------------------------------------------

    def prepare(self, bars: pd.DataFrame) -> None:
        """Precalcula indicadores sobre toda la serie.

        Se ejecuta antes de la simulación. Calcular aquí de forma vectorizada es
        mucho más rápido que barra a barra; el motor garantiza que `on_bar` solo
        ve el índice actual, así que no introduce lookahead mientras los
        indicadores no dependan de datos futuros (nada de `shift(-1)`).
        """

    @abstractmethod
    def on_bar(self, ctx: BarContext) -> Sequence[StrategyAction]:
        """Decide qué hacer en la barra actual. Devolver `()` significa esperar."""

    def on_trade_closed(self, trade: Trade) -> None:
        """Notificación de cierre. Útil para estados tipo martingala o cooldown."""

    def on_finish(self) -> None:
        """Cierre de la corrida."""

    # --- Requisitos ---------------------------------------------------------

    @property
    def warmup_bars(self) -> int:
        """Barras iniciales que el motor debe saltar (indicadores sin valor)."""
        return 0

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": dict(self.params)}
