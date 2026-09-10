"""Punto de composición de la capa CRT: de las velas a los estados y al dibujo.

Aquí, y sólo aquí, se juntan las piezas: las velas que ha cargado el explorador,
las reglas que las leen (`domain/crt`) y la estructura que el explorador sabe
dibujar. Ninguna de las tres conoce a las otras dos.

Hay ahora DOS temporalidades y una regla que las cruza:

  · los **rangos diarios**, que dan el sesgo —hacia dónde va el precio—;
  · los **rangos H4**, detectados con el MISMO detector y el mismo ciclo de
    vida, que dicen cuándo el precio va enfocado hacia ese sesgo;
  · la **alineación**, que al cierre de cada vela H4 deja el sistema en uno de
    cuatro estados.

Es también donde se resuelve lo único que el dominio no puede saber: **cuándo
cerró cada vela**. El detector habla de velas —la `i-1`, la `i`, la `j`— y las
velas van etiquetadas al inicio de su intervalo; el replay necesita el instante
del cierre para no enseñar un rango antes de que existiera, y la alineación lo
necesita para no leer un rango diario a media sesión. Ese instante sale de la
misma rejilla que agregó las velas, que con ancla de sesión no es sumar 24
horas.

Y es donde se arma `build_h4`: el corte de sesión de las 17:00 de Nueva York ya
está escrito una vez en el agregador —con el horario de verano resuelto de
verdad—, así que aquí se le pide a él las velas y se les añade la etiqueta
`h4_open_ny` que el dominio necesita para el filtro horario. Reescribir el corte
dentro del dominio serían dos implementaciones del mismo horario esperando a
divergir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from chronos.application.chart.config import DAILY, H4, AggregationConfig
from chronos.domain.crt.alignment import (
    LATEST,
    MODES,
    STATES,
    AlignmentDelays,
    StateSummary,
    alignment_delays,
    compute_state_h4,
    describe_mode,
    empty_states,
    mark_time_filter,
    summarize_states,
)
from chronos.domain.crt.data_quality import DailyCoverage, daily_coverage
from chronos.domain.crt.ranges import (
    ACTIVE,
    BULLISH,
    NO_TARGET,
    RangeParams,
    RangeSummary,
    current_bias,
    detect_ranges,
    empty_ranges,
    summarize,
)
from chronos.domain.crt.timeframes import H4_OPEN_NY, describe_key_opens, with_h4_opens
from chronos.domain.errors import DomainError
from chronos.infrastructure.market.aggregation import bar_closes
from chronos.infrastructure.market.chart_run import ChartRun, SymbolBars
from chronos.infrastructure.reporting.explorer import AlignmentLayer, RangeLayer

#: La temporalidad del sesgo. Es la que manda: dice hacia dónde va el precio.
RANGE_TIMEFRAME = DAILY

#: La temporalidad del enfoque. Mismo detector, mismo ciclo de vida; lo que
#: cambia es para qué se lee.
FOCUS_TIMEFRAME = H4

#: Con qué parámetros se miran hoy los rangos: TODOS, en las dos temporalidades.
#: El filtro de ruido está escrito y apagado a propósito —primero se mira lo que
#: hay y después se decide qué sobra—, y ningún rango expira. El día que un
#: valor tenga que ser distinto por par, esto se va a un YAML propio de la
#: estrategia; mientras sea el mismo para los cuatro, vive aquí y se imprime en
#: cada corrida.
DEFAULT_PARAMS = RangeParams()


@dataclass(frozen=True, slots=True)
class AlignmentParams:
    """Cuánto se exige para dar por enfocado el precio, y a qué horas.

    Los dos valores por defecto son los del paso: manda el rango H4 vivo más
    reciente y **el filtro horario está apagado**. `strict` está implementado
    para poder comparar los dos modos en el mismo histórico, no porque haya
    decidido nada todavía.
    """

    #: `latest` o `strict`.
    mode: str = LATEST
    #: Aperturas de vela H4, en hora de Nueva York, que se aceptan como válidas
    #: para alinear. `None` = las seis.
    key_h4_opens_ny: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise DomainError(
                f"alignment_mode desconocido: {self.mode!r}. Disponibles: {', '.join(MODES)}"
            )

    def describe(self) -> str:
        return f"{describe_mode(self.mode)} · {describe_key_opens(self.key_h4_opens_ny)}"


DEFAULT_ALIGNMENT = AlignmentParams()


@dataclass(frozen=True, slots=True)
class SymbolRanges:
    """Todo lo que se sabe de un par: lo que se dibuja y lo que se cuenta."""

    symbol: str
    name: str
    #: Con cuántas cifras se escribe el precio de este par: el target de un
    #: sesgo no se lee con dieciséis decimales de float.
    decimals: int
    #: Rangos diarios con las columnas que espera el explorador.
    frame: pd.DataFrame
    summary: RangeSummary
    #: El sesgo a la última vela diaria cerrada del histórico.
    bias: dict[str, Any]
    #: Días de mercado que faltan en el histórico diario de este par.
    coverage: DailyCoverage
    #: Marca de la última vela diaria cerrada, que es el «hoy» de este par.
    as_of: pd.Timestamp | None
    #: Rangos H4, con las mismas columnas más la hora de apertura y la marca del
    #: filtro horario.
    h4_frame: pd.DataFrame = field(default_factory=empty_ranges)
    h4_summary: RangeSummary = field(default_factory=lambda: summarize(empty_ranges()))
    #: El estado del sistema al cierre de cada vela H4, en el modo que se dibuja.
    states: pd.DataFrame = field(default_factory=empty_states)
    #: Lo mismo en cada uno de los modos, para poder compararlos.
    states_by_mode: dict[str, pd.DataFrame] = field(default_factory=dict)


def build_symbol_ranges(
    item: SymbolBars,
    aggregation: AggregationConfig,
    params: RangeParams = DEFAULT_PARAMS,
    alignment: AlignmentParams = DEFAULT_ALIGNMENT,
) -> SymbolRanges:
    """Detecta los rangos de las dos temporalidades y cruza la alineación."""
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
    drawn = _for_drawing(ranges, index, RANGE_TIMEFRAME, aggregation)

    h4_frame, states_by_mode = _focus(item, aggregation, params, alignment, drawn)
    return SymbolRanges(
        symbol=item.symbol,
        name=item.name,
        decimals=item.decimals,
        frame=drawn,
        summary=summarize(ranges, as_of),
        bias=current_bias(ranges, as_of),
        coverage=_coverage(index, aggregation),
        as_of=as_of,
        h4_frame=h4_frame,
        h4_summary=summarize(h4_frame),
        states=states_by_mode.get(alignment.mode, empty_states()),
        states_by_mode=states_by_mode,
    )


def build_ranges(
    run: ChartRun,
    params: RangeParams = DEFAULT_PARAMS,
    alignment: AlignmentParams = DEFAULT_ALIGNMENT,
) -> tuple[SymbolRanges, ...]:
    """Los rangos y los estados de todos los pares de la corrida, en su orden."""
    return tuple(
        build_symbol_ranges(item, run.config.aggregation, params, alignment)
        for item in run.symbols
    )


def build_h4(item: SymbolBars, aggregation: AggregationConfig) -> pd.DataFrame:
    """Las velas H4 de un par, etiquetadas con su hora de apertura en Nueva York.

    El resampleo lo hace el agregador del proyecto, que ya corta la sesión a las
    17:00 de Nueva York con el horario de verano real y trocea cada cuatro
    horas; aquí sólo se le añade la etiqueta. Las velas siguen indexadas por su
    APERTURA, que es la convención de barras del proyecto.

    La etiqueta se pone siempre, pero sólo significa lo que dice con ancla de
    sesión: sin ella H4 es una rejilla fija en UTC y «la vela de las nueve de
    Nueva York» no cae a la misma hora todo el año.
    """
    bars = item.frames.get(FOCUS_TIMEFRAME)
    if bars is None or bars.empty:
        return pd.DataFrame()
    timezone = aggregation.session_anchor
    return with_h4_opens(bars) if timezone is None else with_h4_opens(bars, timezone.timezone)


def alignment_summary(item: SymbolRanges, mode: str) -> StateSummary:
    """Cuánto tiempo pasó el sistema en cada estado, en ese modo."""
    return summarize_states(item.states_by_mode.get(mode, empty_states()))


def alignment_waiting(item: SymbolRanges, mode: str) -> AlignmentDelays:
    """Velas H4 que cada rango diario completado esperó a que H4 se alinease."""
    return alignment_delays(item.states_by_mode.get(mode, empty_states()), item.frame)


def range_layer(
    items: tuple[SymbolRanges, ...], params: RangeParams = DEFAULT_PARAMS
) -> RangeLayer:
    """Empaqueta los rangos diarios para el explorador, que no sabe calcularlos."""
    return RangeLayer(
        timeframe=RANGE_TIMEFRAME,
        description=params.describe(),
        active_status=ACTIVE,
        frames={item.symbol: item.frame for item in items if not item.frame.empty},
    )


def alignment_layer(
    items: tuple[SymbolRanges, ...],
    params: RangeParams = DEFAULT_PARAMS,
    alignment: AlignmentParams = DEFAULT_ALIGNMENT,
) -> AlignmentLayer:
    """Empaqueta los rangos H4 y la línea de estados para el explorador."""
    return AlignmentLayer(
        timeframe=FOCUS_TIMEFRAME,
        description=params.describe(),
        mode=alignment.mode,
        mode_description=describe_mode(alignment.mode),
        time_filter=describe_key_opens(alignment.key_h4_opens_ny),
        filtered=alignment.key_h4_opens_ny is not None,
        states=STATES,
        frames={item.symbol: item.h4_frame for item in items if not item.h4_frame.empty},
        timelines={item.symbol: item.states for item in items if not item.states.empty},
    )


# --- De las velas a los estados ----------------------------------------------------


def _focus(
    item: SymbolBars,
    aggregation: AggregationConfig,
    params: RangeParams,
    alignment: AlignmentParams,
    daily_ranges: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Rangos H4 listos para dibujar y la línea de estados en los dos modos."""
    bars = build_h4(item, aggregation)
    if bars.empty:
        return empty_ranges(), {}

    index = pd.DatetimeIndex(bars.index)
    ranges = detect_ranges(
        bars,
        FOCUS_TIMEFRAME,
        min_size_atr=params.min_size_atr,
        max_candles_alive=params.max_candles_alive,
    )
    drawn = _for_drawing(ranges, index, FOCUS_TIMEFRAME, aggregation)
    if not drawn.empty:
        # La hora de apertura de la vela que CONFIRMÓ el rango: es la que el
        # filtro horario mira, no la de la vela de referencia.
        opens = pd.Series(bars[H4_OPEN_NY].to_numpy(), index=index)
        drawn[H4_OPEN_NY] = opens.reindex(pd.DatetimeIndex(drawn["t_confirm"])).to_numpy()
    drawn = mark_time_filter(drawn, alignment.key_h4_opens_ny)

    closes = pd.DatetimeIndex(bar_closes(index, FOCUS_TIMEFRAME, aggregation))
    states_by_mode = {
        mode: compute_state_h4(
            daily_ranges,
            drawn,
            closes,
            alignment_mode=mode,
            key_h4_opens_ny=alignment.key_h4_opens_ny,
        )
        for mode in MODES
    }
    return _with_alignment(drawn, states_by_mode.get(alignment.mode)), states_by_mode


def _with_alignment(ranges: pd.DataFrame, states: pd.DataFrame | None) -> pd.DataFrame:
    """Añade a cada rango H4 si llegó a alinear con el sesgo diario.

    Tres respuestas y no dos: `True` si alguna vez fue el rango de enfoque,
    `False` si alguna vez fue el que bloqueaba, y nulo si nunca mandó —porque
    no había sesgo diario mientras estuvo vivo, o porque el filtro horario lo
    dejó fuera—. Un «no» que en realidad es «nunca llegó a preguntarse» se lee
    mal en una tabla.
    """
    if ranges.empty:
        return ranges
    frame = ranges.copy()
    if states is None or states.empty:
        frame["aligned_with_d1"] = pd.array([pd.NA] * len(frame), dtype="boolean")
        return frame
    focus = set(states["h4_focus_range_id"].dropna().astype(int).tolist())
    blocking = set(states["h4_blocking_range_id"].dropna().astype(int).tolist())
    identifiers = frame["range_id"].to_numpy(dtype=int)
    frame["aligned_with_d1"] = pd.array(
        [True if key in focus else (False if key in blocking else None) for key in identifiers],
        dtype="boolean",
    )
    return frame


def _for_drawing(
    ranges: pd.DataFrame,
    index: pd.DatetimeIndex,
    timeframe: str,
    aggregation: AggregationConfig,
) -> pd.DataFrame:
    """Añade lo que el dibujo necesita y el dominio no puede saber.

    `bullish` es la dirección como hecho y no como palabra: el explorador
    decide con ella el color y el lado del triángulo sin tener que conocer el
    vocabulario de la estrategia. Los dos `_close` son el instante en que
    cerraron la vela que confirmó el rango y la que lo resolvió: el reloj del
    replay se compara contra ellos, y la alineación también.
    """
    if ranges.empty:
        return ranges
    closes = pd.Series(bar_closes(index, timeframe, aggregation), index=index)
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
