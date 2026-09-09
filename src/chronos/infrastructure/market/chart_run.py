"""Del fichero de precios a las velas de cada par y cada temporalidad.

Es el punto donde se junta todo lo que el explorador necesita y no hay nada más:
se lee el histórico de cada par declarado, se agrega a las temporalidades
pedidas y se devuelve. Ni una regla, ni un indicador, ni una señal.

Un par sin fichero **no rompe la corrida**: se anota por qué no está y los demás
se dibujan igual. Con cuatro pares y un solo histórico descargado, abortar por
los tres que faltan sería no poder mirar el que sí hay.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from chronos.application.chart.config import ExplorerConfig, SymbolConfig
from chronos.domain.errors import DomainError
from chronos.infrastructure.market.aggregation import AggregatedSeries, aggregate
from chronos.infrastructure.market.loader import SidedHistory, load_history


@dataclass(frozen=True, slots=True)
class SymbolBars:
    """Las velas de un par, por temporalidad, con la traza de cómo se armaron."""

    config: SymbolConfig
    history: SidedHistory
    #: Temporalidad -> velas agregadas. Sólo las que el histórico daba para armar.
    series: dict[str, AggregatedSeries]
    #: Las que no se pudieron construir, con el motivo. Se imprimen y se dibujan.
    skipped: tuple[str, ...]

    @property
    def symbol(self) -> str:
        return self.config.symbol.upper()

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def decimals(self) -> int:
        return self.config.decimals

    @property
    def frames(self) -> dict[str, pd.DataFrame]:
        return {timeframe: item.frame for timeframe, item in self.series.items()}

    @property
    def timeframes(self) -> tuple[str, ...]:
        return tuple(self.series)


@dataclass(frozen=True, slots=True)
class ChartRun:
    """Todo lo que se puede dibujar de esta configuración."""

    config: ExplorerConfig
    symbols: tuple[SymbolBars, ...]
    #: Pares declarados que no se pudieron cargar, con el motivo.
    unavailable: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.symbols:
            raise DomainError(
                "Ningún par tiene histórico que dibujar. "
                + (" · ".join(self.unavailable) if self.unavailable else "")
            )

    def of(self, symbol: str) -> SymbolBars:
        key = symbol.strip().upper()
        for item in self.symbols:
            if item.symbol == key:
                return item
        raise DomainError(f"El par {symbol} no está en esta corrida")


def load_symbol(config: ExplorerConfig, symbol: SymbolConfig) -> SymbolBars:
    """Carga y agrega un par. Levanta `DomainError` si su histórico no sirve."""
    history = load_history(symbol, config.price_side)
    series: dict[str, AggregatedSeries] = {}
    skipped: list[str] = []
    for timeframe in config.ordered_timeframes:
        try:
            series[timeframe] = aggregate(history.frame, timeframe, config.aggregation)
        except DomainError as error:
            # Un histórico H1 sirve para el diario, H4 y H1 pero no para M15.
            # Quedarse sin ese gráfico es peor que fabricar velas falsas, que es
            # lo que `aggregate` impide: se anota y el resto se dibuja.
            skipped.append(f"{timeframe}: {error}")
    if not series:
        raise DomainError(
            f"{symbol.symbol}: el histórico no da para ninguna de las temporalidades pedidas"
        )
    return SymbolBars(config=symbol, history=history, series=series, skipped=tuple(skipped))


def build_chart_run(
    config: ExplorerConfig, *, only: Sequence[str] | None = None
) -> ChartRun:
    """Arma las velas de todos los pares declarados (o sólo los de `only`)."""
    wanted = (
        config.declared
        if only is None
        else tuple(config.find(name) for name in only)
    )
    loaded: list[SymbolBars] = []
    unavailable: list[str] = []
    for symbol in config.symbols:
        if symbol not in wanted:
            if only is None and not symbol.is_declared:
                unavailable.append(f"{symbol.symbol}: sin histórico declarado")
            continue
        try:
            loaded.append(load_symbol(config, symbol))
        except DomainError as error:
            unavailable.append(f"{symbol.symbol}: {error}")
    return ChartRun(config=config, symbols=tuple(loaded), unavailable=tuple(unavailable))
