"""Explorador visual de velas, multi-par.

Un único HTML autocontenido —datos, Plotly y lógica embebidos— que se abre con
doble clic y funciona sin conexión. Está pensado para una cosa concreta: poner
el gráfico del proyecto al lado de las capturas de la plataforma del propietario
y poder marcar encima.

Encima del precio hay ahora DOS cosas distintas y el explorador las separa en
todo momento: lo que **calcula el motor** —los rangos CRT diarios, los rangos
CRT de H4 y el estado de alineación entre los dos— y las **marcas a mano** del
propietario. Este módulo no detecta nada: recibe las capas ya calculadas desde
el punto de composición —quien las calcula es `domain/crt`— y las serializa. El
dibujo no puede depender de la estrategia; si lo hiciera, una capa que pinta
acabaría decidiendo.

Del payload sale todo lo que se puede derivar en el navegador: las etiquetas de
los puntos se componen en JavaScript y las marcas de tiempo viajan como minutos
desde la época. Con cuatro pares de ocho años de M15 la diferencia entre hacerlo
así y mandar el texto ya montado son cientos de megabytes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.offline as pyo

from chronos.application.chart.config import (
    DAILY,
    H1,
    H4,
    M15,
    TIMEFRAME_KEYS,
    TIMEFRAME_LABELS,
    MarksConfig,
)
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.market.chart_run import ChartRun, SymbolBars
from chronos.infrastructure.reporting import theme
from chronos.infrastructure.reporting.timezones import session_label

ASSETS = Path(__file__).parent / "assets"
_MARKER = re.compile(r"__[A-Z][A-Z_]*__")

#: Verde alcista, rojo bajista. Se toman de la paleta del proyecto para no
#: introducir hexadecimales sueltos.
BULLISH = theme.SERIES[2]
BEARISH = theme.NEGATIVE

#: El azul del CONTEXTO diario cuando se dibuja sobre H4: las dos líneas del
#: rango que da el sesgo y su objetivo. Va fuera del verde y el rojo de los
#: rangos a propósito, porque encima de H4 conviven las dos temporalidades y hay
#: que poder decir de un vistazo cuál es cuál.
CONTEXT = theme.SERIES[0]

#: Un tono de fondo por estado del sistema. SIN TARGET no lleva ninguno: sin
#: sesgo no hay nada que señalar, y pintar el gráfico entero de un color diría
#: que pasa algo cuando lo que pasa es que no pasa nada.
STATE_COLORS: dict[str, str | None] = {
    "SIN_TARGET": None,
    "TARGET_DEFINIDO": theme.SERIES[3],
    "H4_ALINEADO": BULLISH,
    "H4_EN_CONTRA": BEARISH,
}

#: Cómo se lee cada estado en pantalla. El vocabulario lo pone el motor, igual
#: que `activeStatus`: el explorador lo enseña, no lo decide.
STATE_LABELS: dict[str, str] = {
    "SIN_TARGET": "SIN TARGET",
    "TARGET_DEFINIDO": "TARGET DEFINIDO",
    "H4_ALINEADO": "ALINEADO",
    "H4_EN_CONTRA": "EN CONTRA",
}

#: Los tonos de lo que dibuja el PROPIETARIO a mano encima del gráfico. Van a
#: hues bien separados (318°, 190° y 70°) para poder distinguirse entre ellos, y
#: quedan fuera de la serie categórica y del violeta a propósito: el día que
#: haya capas calculadas, lo que se vea con estos tres colores seguirá siendo lo
#: que ha puesto una mano.
HAND_COLORS: tuple[str, ...] = (theme.MAGENTA, theme.CYAN, theme.OLIVE)

#: Minuto cero de la escala de tiempos del explorador.
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")

#: Columnas que el explorador espera de una capa de rangos ya calculada. Casi
#: todas salen tal cual del detector; tres las añade el punto de composición:
#: `bullish`, que es la dirección como hecho y no como palabra, y los dos
#: `_close`, el instante en que CERRARON la vela que confirmó el rango y la que
#: lo resolvió. Sin ellos el replay no podría saber cuándo se supo cada cosa,
#: porque las velas van etiquetadas al inicio de su intervalo.
RANGE_COLUMNS: tuple[str, ...] = (
    "range_id",
    "direction",
    "bullish",
    "t_ref_open",
    "t_confirm",
    "t_confirm_close",
    "range_high",
    "range_low",
    "manip_extreme",
    "target",
    "invalidation",
    "size",
    "size_atr",
    "status",
    "t_resolved",
    "t_resolved_close",
    "candles_to_resolve",
    "ambiguous_resolution",
)


@dataclass(frozen=True, slots=True)
class RangeLayer:
    """La capa calculada que se le pasa al explorador para que la DIBUJE.

    Llega hecha: el explorador no detecta rangos, no los resuelve y no sabe con
    qué regla se encontraron. Sólo trae lo justo para poder decir en pantalla
    qué se está viendo y con qué parámetros se calculó.
    """

    #: Temporalidad sobre la que se detectaron. Los rangos sólo se dibujan sobre
    #: su propio gráfico: un rango diario encima de M15 taparía la pantalla.
    timeframe: str
    #: Con qué parámetros se detectaron, en una línea, para el estado del HTML.
    description: str
    #: Cómo se llama el estado «vivo». Viene de fuera para que el explorador no
    #: tenga que conocer el vocabulario de la estrategia: él lo enseña, no lo
    #: decide.
    active_status: str
    #: Símbolo -> rangos, con las columnas de `RANGE_COLUMNS`.
    frames: Mapping[str, pd.DataFrame] = field(default_factory=dict)


#: Lo que la capa de alineación añade a cada rango H4 y que el detector no pone:
#: si el filtro horario lo dejó fuera y si llegó a alinear con el sesgo diario.
ALIGNMENT_COLUMNS: tuple[str, ...] = (
    "h4_open_ny",
    "ignored_by_time_filter",
    "aligned_with_d1",
)

#: Columnas de la línea de estados. Una fila por CIERRE de vela H4.
STATE_COLUMNS: tuple[str, ...] = (
    "state",
    "bias_d1",
    "d1_target",
    "d1_range_id",
    "h4_focus_range_id",
    "h4_blocking_range_id",
)


@dataclass(frozen=True, slots=True)
class AlignmentLayer:
    """La segunda capa calculada: los rangos H4 y el estado del sistema.

    Llega hecha, igual que la de los rangos diarios. El explorador no cruza
    nada: recibe una línea de estados con una fila por cierre de vela H4 y la
    pinta. Decidir cuándo el precio va enfocado es de `domain/crt/alignment`.
    """

    #: Temporalidad de los rangos de enfoque y del reloj de los estados.
    timeframe: str
    #: Con qué parámetros se detectaron esos rangos.
    description: str
    #: `latest` o `strict`, y cómo se cuenta en pantalla.
    mode: str
    mode_description: str
    #: Cómo se cuenta el filtro horario, y si está puesto.
    time_filter: str
    filtered: bool
    #: El vocabulario de los estados, en orden. Viene de fuera para que el
    #: explorador no tenga que conocerlo: él lo enseña, no lo decide.
    states: tuple[str, ...]
    #: Símbolo -> rangos H4, con `RANGE_COLUMNS` más `ALIGNMENT_COLUMNS`.
    frames: Mapping[str, pd.DataFrame] = field(default_factory=dict)
    #: Símbolo -> línea de estados, indexada por el cierre de cada vela H4.
    timelines: Mapping[str, pd.DataFrame] = field(default_factory=dict)


def render_explorer(
    run: ChartRun,
    generated_at: datetime | None = None,
    account: dict[str, Any] | None = None,
    ranges: RangeLayer | None = None,
    alignment: AlignmentLayer | None = None,
) -> str:
    """Devuelve el HTML completo del explorador."""
    generated_at = generated_at or SystemClock().now()
    payload = build_payload(run, account, ranges, alignment)
    # El JSON viaja dentro de un <script>: escapar `</` evita que un texto
    # cualquiera pueda cerrar la etiqueta antes de tiempo.
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).replace(
        "</", "<\\/"
    )
    names = " · ".join(item.name for item in run.symbols)
    replacements = {
        "__TITLE__": f"Explorador de velas · {names}",
        "__HEADER__": f"Explorador de velas · {names}",
        "__SUBTITLE__": _subtitle(run, ranges, alignment),
        "__GENERATED__": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "__DATA__": data,
        "__PLOTLY__": pyo.get_plotlyjs(),
        "__EXPLORER_JS__": (ASSETS / "explorer.js").read_text(encoding="utf-8"),
    }
    template = (ASSETS / "explorer.html").read_text(encoding="utf-8")
    # Sustitución en una sola pasada: el contenido insertado (plotly.js incluido)
    # nunca se reescanea en busca de más marcadores.
    return _MARKER.sub(lambda match: replacements.get(match.group(0), match.group(0)), template)


def build_payload(
    run: ChartRun,
    account: dict[str, Any] | None = None,
    ranges: RangeLayer | None = None,
    alignment: AlignmentLayer | None = None,
) -> dict[str, Any]:
    """Serializa la corrida a la estructura que consume el explorador."""
    config = run.config
    anchor = config.aggregation.session_anchor
    payload: dict[str, Any] = {
        "meta": {
            "priceSide": config.price_side,
            "sessionTimezone": config.reporting.session_timezone,
            "sessionTimezoneLabel": session_label(config.reporting.session_timezone),
            "h4OffsetHours": config.aggregation.h4_offset_hours,
            "dSessionStart": config.aggregation.d_session_start,
            #: La plaza que parte el día y su zona IANA. Es el reloj con el que
            #: se etiquetan las velas H4 —«la vela de las nueve»— y el único con
            #: el que se puede escribir a qué hora de la plaza CERRÓ una: en UTC
            #: la misma vela cae a una hora distinta según el mes. Sin ancla de
            #: sesión no hay plaza, y el explorador escribe sólo UTC.
            "anchorPlace": anchor.place if anchor is not None else None,
            "anchorTimezone": anchor.timezone if anchor is not None else None,
        },
        "colors": {
            "bullish": BULLISH,
            "bearish": BEARISH,
            "ink": theme.INK_PRIMARY,
            "muted": theme.INK_MUTED,
            "grid": theme.GRIDLINE,
            "surface": theme.SURFACE,
            "font": theme.FONT_FAMILY,
            #: Un tono por nombre de recuadro y de línea. Los mismos tres tonos
            #: en las dos: lo que separa un recuadro de una línea es el TRAZO
            #: —punteado allí, continuo aquí—, no el color.
            "rects": hand_colors(config.marks.rects),
            "lines": hand_colors(config.marks.lines),
        },
        "labels": dict(TIMEFRAME_LABELS),
        "keys": {
            timeframe: TIMEFRAME_KEYS[timeframe]
            for timeframe in config.ordered_timeframes
            if timeframe in TIMEFRAME_KEYS
        },
        "marks": _marks(config.marks),
        #: La capa calculada: de qué temporalidad son los rangos y con qué
        #: parámetros salieron. Va aparte de las marcas a mano a propósito.
        "crt": _crt_payload(ranges),
        #: La segunda capa calculada: el enfoque de H4 sobre el sesgo diario.
        "h4": _alignment_payload(alignment),
        "symbols": [
            _symbol_payload(item, config.reporting.max_explorer_bars, ranges, alignment)
            for item in run.symbols
        ],
        #: Los pares declarados que no se pudieron cargar. Una pestaña que falta
        #: se lee como que ese par no existe: hay que decir por qué no está.
        "unavailable": list(run.unavailable),
    }
    if account is not None:
        payload["account"] = dict(account)
    return payload


def hand_colors(names: tuple[str, ...]) -> dict[str, str]:
    """Un color de la mano por nombre, en el orden en que se declararon."""
    return {name: HAND_COLORS[index % len(HAND_COLORS)] for index, name in enumerate(names)}


def _marks(marks: MarksConfig) -> dict[str, list[str]]:
    return {"rects": list(marks.rects), "lines": list(marks.lines)}


def _symbol_payload(
    item: SymbolBars,
    max_bars: int,
    ranges: RangeLayer | None = None,
    alignment: AlignmentLayer | None = None,
) -> dict[str, Any]:
    frames = item.frames
    return {
        "id": item.symbol,
        "label": item.name,
        "decimals": item.decimals,
        "side": item.history.side,
        "provenance": item.history.provenance,
        "charts": list(item.timeframes),
        "spans": {
            timeframe: _span_minutes(frame) for timeframe, frame in frames.items()
        },
        "bars": {
            timeframe: _bars_payload(frame, max_bars)
            for timeframe, frame in frames.items()
        },
        #: Temporalidades que el histórico no daba para construir, con el motivo.
        "skipped": list(item.skipped),
        #: Los rangos CRT de este par, ya calculados y resueltos.
        "ranges": _ranges_payload(ranges.frames.get(item.symbol) if ranges else None),
        #: Los rangos CRT de H4 y la línea de estados, por lo mismo.
        "h4Ranges": _h4_ranges_payload(
            alignment.frames.get(item.symbol) if alignment else None
        ),
        "states": _states_payload(
            alignment.timelines.get(item.symbol) if alignment else None,
            alignment.states if alignment else (),
        ),
    }


# --- Rangos CRT ----------------------------------------------------------------

#: Cómo se escribe cada temporalidad cuando acompaña a un dato —«Sesgo D1»,
#: «TP D1»—. Es más corto que la etiqueta de los botones a propósito: va dentro
#: de una frase y encima de una línea del gráfico.
_SHORT_TIMEFRAME: dict[str, str] = {DAILY: "D1", H4: "H4", H1: "H1", M15: "M15"}


def _crt_payload(ranges: RangeLayer | None) -> dict[str, Any]:
    """Qué capa calculada viaja y cómo se escribe. Sin capa, todo apagado."""
    if ranges is None:
        return {"timeframe": None, "label": None, "short": None, "tp": None,
                "activeStatus": None, "description": ""}
    short = _SHORT_TIMEFRAME.get(ranges.timeframe, ranges.timeframe)
    return {
        "timeframe": ranges.timeframe,
        "label": TIMEFRAME_LABELS.get(ranges.timeframe, ranges.timeframe),
        "short": short,
        "tp": f"TP {short}",
        "activeStatus": ranges.active_status,
        "description": ranges.description,
    }


def _ranges_payload(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Un rango por fila, con los instantes en minutos como todo lo demás.

    Viajan dos marcas por cada suceso: la ETIQUETA de la vela —que es lo que se
    dibuja, porque las velas se etiquetan al inicio— y el instante en que esa
    vela CERRÓ, que es cuando el rango se supo. El replay usa la segunda para no
    enseñar nada antes de tiempo.
    """
    if frame is None or frame.empty:
        return []
    missing = [name for name in RANGE_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"A la capa de rangos le faltan columnas: {', '.join(missing)}")

    ref = _epoch_minutes(pd.DatetimeIndex(frame["t_ref_open"]))
    confirm = _epoch_minutes(pd.DatetimeIndex(frame["t_confirm"]))
    known = _epoch_minutes(pd.DatetimeIndex(frame["t_confirm_close"]))
    resolved = _optional_minutes(frame["t_resolved"])
    resolved_close = _optional_minutes(frame["t_resolved_close"])
    highs = _round(frame["range_high"])
    lows = _round(frame["range_low"])
    manip = _round(frame["manip_extreme"])
    target = _round(frame["target"])
    invalidation = _round(frame["invalidation"])
    size = _round(frame["size"])

    return [
        {
            "id": int(identifier),
            "dir": str(frame["direction"].iloc[position]),
            # Hacia dónde mira el rango, como hecho y no como palabra: es lo que
            # decide el color y el lado del triángulo, y el dibujo no tiene por
            # qué saber cómo se escribe «alcista».
            "up": bool(frame["bullish"].iloc[position]),
            "ref": ref[position],
            "confirm": confirm[position],
            "known": known[position],
            "high": highs[position],
            "low": lows[position],
            "manip": manip[position],
            "target": target[position],
            "invalidation": invalidation[position],
            "size": size[position],
            "sizeAtr": _optional_float(frame["size_atr"].iloc[position]),
            "status": str(frame["status"].iloc[position]),
            "resolved": resolved[position],
            "resolvedAt": resolved_close[position],
            "candles": _optional_int(frame["candles_to_resolve"].iloc[position]),
            "ambiguous": bool(frame["ambiguous_resolution"].iloc[position]),
        }
        for position, identifier in enumerate(frame["range_id"].to_numpy(dtype=int))
    ]


def _alignment_payload(alignment: AlignmentLayer | None) -> dict[str, Any]:
    """Qué dice la capa de enfoque y cómo se escribe. Sin capa, todo apagado."""
    if alignment is None:
        return {
            "timeframe": None, "label": None, "short": None, "tp": None,
            "description": "", "mode": None, "modeLabel": "", "timeFilter": "",
            "filtered": False, "states": [], "labels": {}, "colors": {},
            "context": None,
        }
    short = _SHORT_TIMEFRAME.get(alignment.timeframe, alignment.timeframe)
    return {
        "timeframe": alignment.timeframe,
        "label": TIMEFRAME_LABELS.get(alignment.timeframe, alignment.timeframe),
        "short": short,
        "tp": f"TP {short}",
        "description": alignment.description,
        "mode": alignment.mode,
        "modeLabel": alignment.mode_description,
        "timeFilter": alignment.time_filter,
        "filtered": alignment.filtered,
        "states": list(alignment.states),
        "labels": {name: STATE_LABELS.get(name, name) for name in alignment.states},
        "colors": {name: STATE_COLORS.get(name) for name in alignment.states},
        #: El color del contexto diario cuando se dibuja encima de H4.
        "context": CONTEXT,
    }


def _h4_ranges_payload(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Los rangos H4, que son los de siempre más lo que dice la alineación.

    `ignored` es el rango que existe pero no cuenta porque su vela no abre a una
    hora clave, y `aligned` tiene TRES respuestas: alineó, bloqueó, o nunca
    llegó a mandar. Un «no» que en realidad es «nunca se preguntó» se lee mal.
    """
    if frame is None or frame.empty:
        return []
    missing = [name for name in ALIGNMENT_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"A la capa de enfoque le faltan columnas: {', '.join(missing)}")
    items = _ranges_payload(frame)
    ignored = frame["ignored_by_time_filter"].to_numpy(dtype=bool)
    opens = frame["h4_open_ny"].to_numpy(dtype=object)
    aligned = frame["aligned_with_d1"]
    for position, item in enumerate(items):
        # A qué hora de Nueva York abrió la vela que CONFIRMÓ el rango: es la que
        # mira el filtro horario, y la que hace falta para poder discutirlo.
        item["open"] = None if pd.isna(opens[position]) else str(opens[position])
        item["ignored"] = bool(ignored[position])
        value = aligned.iloc[position]
        item["aligned"] = None if pd.isna(value) else bool(value)
    return items


def _states_payload(
    frame: pd.DataFrame | None, states: tuple[str, ...] = ()
) -> dict[str, Any]:
    """La línea de estados, en columnas paralelas y con el estado como número.

    Una fila por CIERRE de vela H4, que es cuando el sistema se reevalúa. Van
    los índices del vocabulario en vez del nombre entero porque con ocho años
    son doce mil filas y el nombre repetido son megabytes de JSON.
    """
    if frame is None or frame.empty:
        return {"t": [], "s": [], "d1": [], "target": [], "focus": [], "block": []}
    missing = [name for name in STATE_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"A la línea de estados le faltan columnas: {', '.join(missing)}")
    codes = {name: position for position, name in enumerate(states or tuple(STATE_LABELS))}
    return {
        "t": _epoch_minutes(pd.DatetimeIndex(frame.index)),
        "s": [codes.get(str(value), 0) for value in frame["state"]],
        "d1": _optional_ints(frame["d1_range_id"]),
        "target": _optional_floats(frame["d1_target"]),
        "focus": _optional_ints(frame["h4_focus_range_id"]),
        "block": _optional_ints(frame["h4_blocking_range_id"]),
    }


def _optional_ints(column: pd.Series) -> list[int | None]:
    return [None if pd.isna(value) else int(value) for value in column]


def _optional_floats(column: pd.Series) -> list[float | None]:
    return [
        None if pd.isna(value) else round(float(value), _PAYLOAD_DECIMALS) for value in column
    ]


def _optional_minutes(column: pd.Series) -> list[int | None]:
    """Minutos de cada marca, y `None` donde no hay ninguna: un rango sin
    resolver no se resolvió en 1970."""
    index = pd.DatetimeIndex(column)
    minutes = _epoch_minutes(pd.DatetimeIndex(index.fillna(_EPOCH)))
    return [None if missing else value for value, missing in zip(minutes, index.isna(), strict=True)]


def _optional_float(value: Any) -> float | None:
    return None if pd.isna(value) else round(float(value), 3)


def _optional_int(value: Any) -> int | None:
    return None if pd.isna(value) else int(value)


# --- Velas ------------------------------------------------------------------


def _bars_payload(frame: pd.DataFrame, max_bars: int) -> dict[str, Any]:
    total = len(frame)
    truncated = 0 < max_bars < total
    if truncated:
        frame = frame.iloc[-max_bars:]
    index = pd.DatetimeIndex(frame.index)
    return {
        "truncated": truncated,
        "total": total,
        "t": _epoch_minutes(index),
        "o": _round(frame["open"]),
        "h": _round(frame["high"]),
        "l": _round(frame["low"]),
        "c": _round(frame["close"]),
    }


#: Con cuántas cifras viajan los precios dentro del JSON. Va por encima de los
#: decimales con los que se DIBUJA cada par —eso lo decide el navegador— para
#: que redondear aquí no pueda mover una vela; lo que hace es cortar la cola de
#: ruido binario del float, que en ocho años de M15 son megabytes de dígitos que
#: nadie va a mirar.
_PAYLOAD_DECIMALS = 6


def _epoch_minutes(index: pd.DatetimeIndex) -> list[int]:
    utc = index.tz_convert("UTC") if index.tz is not None else index.tz_localize("UTC")
    return [int(value) for value in ((utc - _EPOCH) // pd.Timedelta(minutes=1)).to_numpy()]


def _round(series: pd.Series) -> list[float]:
    return [round(float(value), _PAYLOAD_DECIMALS) for value in series.to_numpy(dtype=float)]


def _span_minutes(frame: pd.DataFrame) -> int:
    """Duración de la vela, en minutos, medida sobre las propias velas.

    Es lo que le falta al replay para saber **cuándo** se supo cada cosa. Las
    velas van etiquetadas al inicio del intervalo, así que la vela de `t` no
    cierra hasta `t + span`.

    Se mide con la moda de las diferencias en vez de deducirla del nombre de la
    temporalidad: con ancla de sesión el diario no dura siempre lo mismo y el
    nombre mentiría.
    """
    return int(_bar_span(pd.DatetimeIndex(frame.index)) // pd.Timedelta(minutes=1))


def _bar_span(index: pd.DatetimeIndex) -> pd.Timedelta:
    if len(index) < 2:
        return pd.Timedelta(hours=4)
    deltas = index.to_series().diff().dropna()
    return pd.Timedelta(deltas.mode().iloc[0]) if not deltas.empty else pd.Timedelta(hours=4)


def _subtitle(
    run: ChartRun,
    ranges: RangeLayer | None = None,
    alignment: AlignmentLayer | None = None,
) -> str:
    config = run.config
    pares = ", ".join(
        f"{item.name} ({', '.join(TIMEFRAME_LABELS.get(tf, tf) for tf in item.timeframes)})"
        for item in run.symbols
    )
    faltan = f" · sin embeber: {', '.join(run.unavailable)}" if run.unavailable else ""
    if ranges is None:
        capa = "sin ninguna capa calculada: sólo velas y lo que marques a mano"
    else:
        etiqueta = TIMEFRAME_LABELS.get(ranges.timeframe, ranges.timeframe)
        capa = (
            f"capa calculada: rangos CRT de {etiqueta} ({ranges.description}); "
            "lo demás lo marcas a mano"
        )
    if alignment is not None:
        enfoque = TIMEFRAME_LABELS.get(alignment.timeframe, alignment.timeframe)
        capa = (
            f"capas calculadas: rangos CRT de {etiqueta} y de {enfoque} "
            f"({ranges.description if ranges else ''}) y la alineación entre las dos "
            f"({alignment.mode_description}); lo demás lo marcas a mano"
        )
    return (
        f"{pares} · lado {config.price_side} · día desde "
        f"{config.aggregation.describe_daily_start()} · {capa}{faltan}"
    )


def payload_size(payload: dict[str, Any]) -> int:
    """Bytes del JSON embebido. Útil para vigilar que el fichero no se dispare."""
    return len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))


def bar_counts(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        symbol["id"]: {chart: len(bars["t"]) for chart, bars in symbol["bars"].items()}
        for symbol in payload["symbols"]
    }
