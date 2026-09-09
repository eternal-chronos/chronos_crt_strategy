"""Intenciones que una estrategia puede emitir en una barra.

La estrategia NO ejecuta: describe qué quiere. El bróker simulado decide si es
posible, a qué precio y con qué costes. Esa separación es lo que permite
reutilizar la misma estrategia en backtest, demo y live sin tocarla.
"""

from __future__ import annotations

from dataclasses import dataclass

from chronos.domain.enums import ExitReason, Side
from chronos.domain.errors import InvalidOrder


@dataclass(frozen=True, slots=True)
class EntrySignal:
    """Petición de apertura de posición.

    El tamaño se resuelve así, por orden de prioridad:
      1. `volume` si viene informado (lotes explícitos).
      2. `risk_fraction` sobre el equity, usando la distancia al stop.
      3. La política de sizing configurada en el backtest.
    """

    side: Side
    stop_loss: float | None = None
    take_profit: float | None = None
    stop_distance: float | None = None
    take_profit_distance: float | None = None
    volume: float | None = None
    risk_fraction: float | None = None
    tag: str = ""
    comment: str = ""

    def __post_init__(self) -> None:
        if self.volume is not None and self.volume <= 0:
            raise InvalidOrder("El volumen debe ser positivo")
        if self.risk_fraction is not None and not 0 < self.risk_fraction < 1:
            raise InvalidOrder("risk_fraction debe estar en (0, 1)")
        if self.stop_loss is not None and self.stop_distance is not None:
            raise InvalidOrder("Indica el stop como precio o como distancia, no ambos")
        if self.take_profit is not None and self.take_profit_distance is not None:
            raise InvalidOrder("Indica el objetivo como precio o como distancia, no ambos")
        if self.stop_distance is not None and self.stop_distance <= 0:
            raise InvalidOrder("stop_distance debe ser positiva")
        if self.take_profit_distance is not None and self.take_profit_distance <= 0:
            raise InvalidOrder("take_profit_distance debe ser positiva")

    def levels(self, entry_price: float) -> tuple[float | None, float | None]:
        """Resuelve SL y TP a precios absolutos sobre el precio de ejecución real.

        Expresar los niveles como distancia es lo robusto: la estrategia decide
        sobre el cierre de la barra N, pero el fill ocurre en la apertura de la
        N+1, y un nivel absoluto calculado sobre el cierre puede quedarse del
        lado equivocado si hay hueco.
        """
        stop = self.stop_loss
        if self.stop_distance is not None:
            stop = entry_price - self.side.sign * self.stop_distance
        target = self.take_profit
        if self.take_profit_distance is not None:
            target = entry_price + self.side.sign * self.take_profit_distance
        return stop, target

    def validate_against(self, reference_price: float) -> None:
        """Comprueba que SL y TP quedan del lado correcto del precio de entrada."""
        stop, target = self.levels(reference_price)
        if stop is not None:
            if self.side is Side.BUY and stop >= reference_price:
                raise InvalidOrder("En largo, el stop loss debe estar por debajo de la entrada")
            if self.side is Side.SELL and stop <= reference_price:
                raise InvalidOrder("En corto, el stop loss debe estar por encima de la entrada")
        if target is not None:
            if self.side is Side.BUY and target <= reference_price:
                raise InvalidOrder("En largo, el take profit debe estar por encima de la entrada")
            if self.side is Side.SELL and target >= reference_price:
                raise InvalidOrder("En corto, el take profit debe estar por debajo de la entrada")


@dataclass(frozen=True, slots=True)
class ExitSignal:
    """Cierre de una posición abierta. Sin `position_id`, cierra todas."""

    reason: ExitReason = ExitReason.SIGNAL
    position_id: int | None = None
    tag: str = ""


@dataclass(frozen=True, slots=True)
class ModifyStops:
    """Reubicación de stop loss / take profit (trailing, break-even...)."""

    position_id: int | None = None
    stop_loss: float | None = None
    take_profit: float | None = None

    def __post_init__(self) -> None:
        if self.stop_loss is None and self.take_profit is None:
            raise InvalidOrder("ModifyStops necesita al menos un nivel")


StrategyAction = EntrySignal | ExitSignal | ModifyStops
