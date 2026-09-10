"""Punto de composición de la capa CRT: de las velas diarias al dibujo.

Aquí, y sólo aquí, se juntan las tres piezas: las velas que ha cargado el
explorador, la regla que las lee (`domain/crt`) y la estructura que el
explorador sabe dibujar. Ninguna de las tres conoce a las otras dos.

Es también donde se resuelve lo único que el dominio no puede saber: **cuándo
cerró cada vela**. El detector habla de velas —la `i-1`, la `i`, la `j`— y las
velas van etiquetadas al inicio de su intervalo; el replay necesita el instante
del cierre para no enseñar un rango antes de que existiera. Ese instante sale de
la misma rejilla que agregó las velas, que con ancla de sesión no es sumar 24
horas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from chronos.application.chart.config import DAILY, AggregationConfig
from chronos.domain.crt.data_quality import DailyCoverage, daily_coverage
from chronos.domain.crt.ranges import (
    ACTIVE,
    BULLISH,
    NO_TARGET,
    RangeParams,
    RangeSummary,
    current_bias,
    detect_ranges,
    summarize,
)
from chronos.infrastructure.market.aggregation import bar_closes
from chronos.infrastructure.market.chart_run import ChartRun, SymbolBars
from chronos.infrastructure.reporting.explorer import RangeLayer

#: La temporalidad de este paso. Sólo el diario: no hay ni H4, ni H1, ni M15, ni
#: entradas, ni stops. El detector es agnóstico y admitirá las demás el día que
#: las haya.
RANGE_TIMEFRAME = DAILY

#: Con qué parámetros se miran hoy los rangos: TODOS. El filtro de ruido está
#: escrito y apagado a propósito —primero se mira lo que hay y después se decide
#: qué sobra—, y ningún rango expira. El día que un valor tenga que ser distinto
#: por par, esto se va a un YAML propio de la estrategia; mientras sea el mismo
#: para los cuatro, vive aquí y se imprime en cada corrida.
DEFAULT_PARAMS = RangeParams()


@dataclass(frozen=True, slots=True)
class SymbolRanges:
    """Todo lo que se sabe de los rangos de un par: lo que se dibuja y lo que se cuenta."""

    symbol: str
    name: str
    #: Con cuántas cifras se escribe el precio de este par: el target de un
    #: sesgo no se lee con dieciséis decimales de float.
    decimals: int
    #: Rangos con las columnas que espera el explorador.
    frame: pd.DataFrame
    summary: RangeSummary
    #: El sesgo a la última vela cerrada del histórico.
    bias: dict[str, Any]
    #: Días de mercado que faltan en el histórico diario de este par.
    coverage: DailyCoverage
    #: Marca de la última vela diaria cerrada, que es el «hoy» de este par.
    as_of: pd.Timestamp | None


def build_symbol_ranges(
    item: SymbolBars, aggregation: AggregationConfig, params: RangeParams = DEFAULT_PARAMS
) -> SymbolRanges:
    """Detecta los rangos de un par y los deja listos para dibujar y para contar."""
    daily = item.frames.get(RANGE_TIMEFRAME)
    if daily is None or daily.empty:
        return SymbolRanges(
            symbol=item.symbol,
            name=item.name,
            decimals=item.decimals,
            frame=pd.DataFrame(),
            summary=summarize(pd.DataFrame()),
            bias={"direction": NO_TARGET},
            coverage=_coverage(pd.DatetimeIndex([], tz="UTC"), aggregation),
            as_of=None,
        )

    index = pd.DatetimeIndex(daily.index)
    ranges = detect_ranges(
        daily,
        RANGE_TIMEFRAME,
        min_size_atr=params.min_size_atr,
        max_candles_alive=params.max_candles_alive,
    )
    as_of = pd.Timestamp(index[-1])
    return SymbolRanges(
        symbol=item.symbol,
        name=item.name,
        decimals=item.decimals,
        frame=_for_drawing(ranges, index, aggregation),
        summary=summarize(ranges, as_of),
        bias=current_bias(ranges, as_of),
        coverage=_coverage(index, aggregation),
        as_of=as_of,
    )


def build_ranges(
    run: ChartRun, params: RangeParams = DEFAULT_PARAMS
) -> tuple[SymbolRanges, ...]:
    """Los rangos de todos los pares de la corrida, en su orden."""
    return tuple(
        build_symbol_ranges(item, run.config.aggregation, params) for item in run.symbols
    )


def range_layer(
    items: tuple[SymbolRanges, ...], params: RangeParams = DEFAULT_PARAMS
) -> RangeLayer:
    """Empaqueta los rangos para el explorador, que no sabe calcularlos."""
    return RangeLayer(
        timeframe=RANGE_TIMEFRAME,
        description=params.describe(),
        active_status=ACTIVE,
        frames={item.symbol: item.frame for item in items if not item.frame.empty},
    )


def _for_drawing(
    ranges: pd.DataFrame, index: pd.DatetimeIndex, aggregation: AggregationConfig
) -> pd.DataFrame:
    """Añade lo que el dibujo necesita y el dominio no puede saber.

    `bullish` es la dirección como hecho y no como palabra: el explorador
    decide con ella el color y el lado del triángulo sin tener que conocer el
    vocabulario de la estrategia. Los dos `_close` son el instante en que
    cerraron la vela que confirmó el rango y la que lo resolvió: el reloj del
    replay se compara contra ellos, nunca contra la etiqueta de la vela.
    """
    if ranges.empty:
        return ranges
    closes = pd.Series(bar_closes(index, RANGE_TIMEFRAME, aggregation), index=index)
    frame = ranges.copy()
    frame["bullish"] = frame["direction"] == BULLISH
    frame["t_confirm_close"] = closes.reindex(pd.DatetimeIndex(frame["t_confirm"])).array
    frame["t_resolved_close"] = closes.reindex(pd.DatetimeIndex(frame["t_resolved"])).array
    return frame


def _coverage(index: pd.DatetimeIndex, aggregation: AggregationConfig) -> DailyCoverage:
    """Qué sesiones faltan, con el mismo calendario con el que se agregó el día."""
    anchor = aggregation.session_anchor
    return daily_coverage(
        index,
        anchor.timezone if anchor is not None else "UTC",
        aggregation.session_start_time(),
    )
