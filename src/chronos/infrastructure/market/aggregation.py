"""Agregación M1 → H3/H4/H6/H12/Diario con offset configurable (§1.2).

El resultado del módulo depende íntegramente del offset: nada aquí está fijado
en el código, y cambiar `h4_offset_hours` o `d_session_start` cambia las velas
de verdad (hay un test que lo comprueba).

Convenciones, iguales a las de `infrastructure.data.frames`:
    open  = open de la primera M1 del intervalo
    high  = máximo de los high
    low   = mínimo de los low
    close = close de la última M1
    label = inicio del intervalo
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from chronos.application.chart.config import (
    DAILY,
    H1,
    H3,
    H4,
    H6,
    H12,
    M15,
    AggregationConfig,
    SessionAnchor,
    by_size,
)
from chronos.domain.errors import DomainError

#: Paso que se asume cuando el histórico es demasiado corto para deducirlo.
DEFAULT_BASE_STEP = pd.Timedelta(minutes=1)

#: Duración de cada trozo de sesión: H3, H4, H6 y H12 son la sesión troceada.
SESSION_STEPS: dict[str, pd.Timedelta] = {
    H3: pd.Timedelta(hours=3),
    H4: pd.Timedelta(hours=4),
    H6: pd.Timedelta(hours=6),
    H12: pd.Timedelta(hours=12),
}

_AGGREGATION: dict[Hashable, Any] = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}


@dataclass(frozen=True, slots=True)
class AggregatedSeries:
    """Barras de una temporalidad, con la traza de la agregación que las produjo."""

    timeframe: str
    frame: pd.DataFrame
    freq: str
    offset: pd.Timedelta
    #: Barra final descartada por estar incompleta, si la hubo.
    dropped_incomplete: pd.Timestamp | None
    #: Ancla de sesión que produjo las velas, si no fue una rejilla fija en UTC.
    session: SessionAnchor | None = None

    @property
    def description(self) -> str:
        if self.session is not None:
            return (
                f"{self.timeframe}: sesión que abre a las {self.session.describe()}"
                + (
                    f", troceada cada {_humanize(SESSION_STEPS[self.timeframe])}"
                    if self.timeframe in SESSION_STEPS
                    else ""
                )
            )
        offset = self.offset.total_seconds() / 3600.0
        return f"{self.timeframe}: freq={self.freq}, offset={offset:g} h desde 00:00 UTC"


def aggregate_all(
    frame: pd.DataFrame, config: AggregationConfig, timeframes: Iterable[str]
) -> dict[str, AggregatedSeries]:
    """Construye las temporalidades pedidas, de mayor a menor."""
    return {
        timeframe: aggregate(frame, timeframe, config) for timeframe in by_size(timeframes)
    }


def aggregate(
    frame: pd.DataFrame, timeframe: str, config: AggregationConfig
) -> AggregatedSeries:
    """Reagrupa el histórico M1 a `timeframe` aplicando el offset configurado."""
    if frame.empty:
        raise DomainError("No hay barras M1 que agregar")
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        raise DomainError("La agregación exige un índice tz-aware en UTC")

    working = frame.copy()
    working.index = index.tz_convert("UTC")
    if "volume" not in working.columns:
        working["volume"] = 0.0
    utc_index = pd.DatetimeIndex(working.index)

    anchor = _anchor_for(timeframe, config)
    freq, offset, span = _bins(timeframe, config)

    # Sin este control, pedir M15 a un histórico H1 no daría error: el resampleo
    # crearía huecos, `dropna` los quitaría y quedarían velas etiquetadas como
    # M15 que en realidad son las de una hora. Datos falsos y silenciosos.
    step = base_step(utc_index)
    if step > span:
        raise DomainError(
            f"No se puede construir {timeframe} ({_humanize(span)}) desde un histórico de "
            f"{_humanize(step)}: hace falta un histórico más fino "
            f"(`chronos data dukascopy -g m1`)"
        )

    if anchor is None:
        resampled = working.resample(
            freq, label="left", closed="left", origin="epoch", offset=offset
        ).agg(_AGGREGATION)
        end_of_last = None
    else:
        labels, ends = session_buckets(utc_index, timeframe, anchor)
        resampled = working.groupby(labels).agg(_AGGREGATION)
        resampled.index = pd.DatetimeIndex(resampled.index, name=utc_index.name)
        end_of_last = pd.Timestamp(ends[-1])

    resampled = resampled.dropna(subset=["open", "high", "low", "close"])

    dropped = None
    if not resampled.empty:
        last_label = pd.Timestamp(resampled.index[-1])
        # La última barra de entrada cubre su propio paso: con M1 el histórico
        # llega un minuto más allá de su última marca, con H1 una hora. Dar por
        # supuesto el minuto descartaría una vela H4 buena cuando la entrada no
        # es M1.
        covered_until = pd.Timestamp(utc_index[-1]) + step
        # Con ancla de sesión el cubo no mide siempre lo mismo: los dos días del
        # año en que el reloj se mueve, la sesión dura 23 o 25 horas.
        closes_at = end_of_last if end_of_last is not None else last_label + span
        if covered_until < closes_at:
            dropped = last_label
            resampled = resampled.iloc[:-1]

    return AggregatedSeries(
        timeframe=timeframe,
        frame=resampled,
        freq=freq,
        offset=offset,
        dropped_incomplete=dropped,
        session=anchor,
    )


def _anchor_for(timeframe: str, config: AggregationConfig) -> SessionAnchor | None:
    """Ancla de sesión aplicable a esta temporalidad.

    Sólo el diario, H12, H6, H4 y H3 se anclan a la sesión: la rejilla de M15 y H1
    —cuartos de hora y horas en punto— es la misma en todas las plataformas y no
    depende de dónde empiece el día. Cuando hay ancla, `h4_offset_hours` no pinta
    nada: H3, H4, H6 y H12 arrancan con la sesión, que es lo que hace la plataforma del
    propietario.
    """
    if timeframe not in (DAILY, H12, H6, H4, H3):
        return None
    return config.session_anchor


def session_buckets(
    index: pd.DatetimeIndex, timeframe: str, anchor: SessionAnchor
) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Cubo de sesión de cada barra base, y el instante en que ese cubo cierra.

    La sesión se decide con el **reloj de la plaza**, no restando horas en UTC:
    a lo largo del año Nueva York abre siempre a la misma hora local, de modo que
    en UTC el corte se mueve una hora dos veces. Restar un desplazamiento fijo
    daría bien once meses y mal los otros dos, y encima etiquetaría mal la última
    hora de la sesión de 25 horas del cambio de octubre.
    """
    opening = pd.Timedelta(hours=anchor.at.hour, minutes=anchor.at.minute)
    local = index.tz_convert(anchor.timezone).tz_localize(None)
    # La sesión es la de hoy si el reloj local ya pasó la apertura; si no, la de
    # ayer. Es una comparación de reloj de pared: así sale bien tanto la hora que
    # se repite en octubre como la que no existe en marzo.
    day = local.normalize()
    before_open = local < day + opening
    session_day = day - pd.to_timedelta(before_open.astype("int8"), unit="D")

    starts = _localized(session_day + opening, anchor)
    next_starts = _localized(session_day + pd.Timedelta(days=1) + opening, anchor)
    if timeframe == DAILY:
        return starts, next_starts

    piece = SESSION_STEPS[timeframe]
    steps = (index - starts) // piece
    labels = pd.DatetimeIndex(starts + steps * piece)
    # El último trozo de una sesión de 23 o 25 horas se queda corto.
    closing = labels + piece
    ends = pd.DatetimeIndex(closing.where(closing < next_starts, next_starts))
    return labels, ends


def _localized(naive: pd.DatetimeIndex, anchor: SessionAnchor) -> pd.DatetimeIndex:
    """Pasa una hora de pared de la plaza a UTC.

    Sin `ambiguous`/`nonexistent` por defecto: si alguien configurase una
    apertura dentro del salto del reloj, tiene que fallar y verse, no elegirse
    una hora por su cuenta.
    """
    return pd.DatetimeIndex(naive).tz_localize(anchor.timezone).tz_convert("UTC")


def minutes_covered(
    frame: pd.DataFrame,
    timeframe: str,
    config: AggregationConfig,
    target: pd.DatetimeIndex,
) -> np.ndarray:
    """Barras del histórico base que caen dentro de cada vela agregada.

    Con un histórico M1 son minutos de mercado. Sirve para distinguir una vela
    diaria de verdad —unos 1.400 minutos— de la que sólo contiene la hora del
    domingo por la tarde, que el corte de sesión deja suelta y que la máquina
    trata como cualquier otra: abre piernas, constituye y rompe.

    Se calcula con la misma rejilla que `aggregate`, así que cada posición del
    resultado corresponde a la misma posición de las velas agregadas.
    """
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        raise DomainError("El recuento de minutos exige un índice tz-aware en UTC")
    working = frame["close"].copy()
    working.index = index.tz_convert("UTC")

    anchor = _anchor_for(timeframe, config)
    if anchor is None:
        freq, offset, _span = _bins(timeframe, config)
        counts = working.resample(
            freq, label="left", closed="left", origin="epoch", offset=offset
        ).count()
    else:
        labels, _ends = session_buckets(pd.DatetimeIndex(working.index), timeframe, anchor)
        counts = working.groupby(labels).count()
        counts.index = pd.DatetimeIndex(counts.index)
    return counts.reindex(target).fillna(0).to_numpy(dtype=int)


def base_step(index: pd.DatetimeIndex) -> pd.Timedelta:
    """Paso nativo del histórico, deducido del salto más frecuente.

    La moda y no el mínimo: el histórico tiene huecos de fin de semana y minutos
    sin negociar, y cualquiera de los dos extremos daría un paso equivocado.
    """
    if len(index) < 2:
        return DEFAULT_BASE_STEP
    deltas = index.to_series().diff().dropna()
    if deltas.empty:
        return DEFAULT_BASE_STEP
    return pd.Timedelta(deltas.mode().iloc[0])


def _humanize(step: pd.Timedelta) -> str:
    minutes = step.total_seconds() / 60
    return f"{minutes / 60:g} h" if minutes >= 60 else f"{minutes:g} min"


def _bins(timeframe: str, config: AggregationConfig) -> tuple[str, pd.Timedelta, pd.Timedelta]:
    """Frecuencia, desplazamiento y duración del intervalo de una temporalidad.

    M15 y H1 no llevan desplazamiento: su rejilla —cuartos de hora y horas en
    punto— es la misma en todas las plataformas. H3, H4, H6, H12 y el diario admiten
    ajuste, que es donde discrepan.
    """
    if timeframe == M15:
        return "15min", pd.Timedelta(0), pd.Timedelta(minutes=15)
    if timeframe == H1:
        return "1h", pd.Timedelta(0), pd.Timedelta(hours=1)
    if timeframe == H3:
        # Sin ancla de sesión, H3 trocea el día desde su hora de arranque: con
        # el día a las 22:00 UTC, las velas abren a las 22:00, 01:00, 04:00...
        start = config.session_start_time()
        return (
            "3h",
            pd.Timedelta(hours=start.hour % 3, minutes=start.minute),
            pd.Timedelta(hours=3),
        )
    if timeframe == H4:
        # El desplazamiento sólo tiene sentido dentro del ciclo de 4 horas:
        # un offset de 5 h produce exactamente las mismas velas que uno de 1 h.
        return (
            "4h",
            pd.Timedelta(hours=config.h4_effective_offset_hours),
            pd.Timedelta(hours=4),
        )
    if timeframe == H6:
        # Sin ancla de sesión, H6 trocea el día desde su hora de arranque: con
        # el día a las 22:00 UTC, las velas abren a las 22:00, 04:00, 10:00...
        start = config.session_start_time()
        return (
            "6h",
            pd.Timedelta(hours=start.hour % 6, minutes=start.minute),
            pd.Timedelta(hours=6),
        )
    if timeframe == H12:
        # Sin ancla de sesión, H12 parte el día por su hora de arranque: con el
        # día a las 22:00 UTC, las velas abren a las 22:00 y a las 10:00.
        start = config.session_start_time()
        return (
            "12h",
            pd.Timedelta(hours=start.hour % 12, minutes=start.minute),
            pd.Timedelta(hours=12),
        )
    if timeframe == DAILY:
        start = config.session_start_time()
        # "24h" y no "1D": pandas ignora `origin` y `offset` con frecuencias que
        # no son Tick-like, así que con "1D" el inicio de sesión no tendría
        # ningún efecto. Todo el pipeline es UTC, sin horario de verano, de modo
        # que 24 horas y un día natural son lo mismo.
        return (
            "24h",
            pd.Timedelta(hours=start.hour, minutes=start.minute),
            pd.Timedelta(hours=24),
        )
    raise DomainError(f"Temporalidad no soportada por el módulo de estructura: {timeframe}")
