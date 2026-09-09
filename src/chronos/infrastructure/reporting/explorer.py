"""Explorador visual de velas, multi-par.

Un único HTML autocontenido —datos, Plotly y lógica embebidos— que se abre con
doble clic y funciona sin conexión. Está pensado para una cosa concreta: poner
el gráfico del proyecto al lado de las capturas de la plataforma del propietario
y poder marcar encima.

**No dibuja ninguna estrategia porque todavía no hay ninguna.** Lo que sale del
payload son velas y nada más: cuatro pares, las temporalidades que la
configuración pida y los nombres con los que el propietario marca a mano. El día
que haya una capa calculada, se añade aquí y se enciende en el JavaScript; hasta
entonces el explorador dice en cada dibujo que lo único que hay encima del precio
lo ha puesto una mano.

Del payload sale todo lo que se puede derivar en el navegador: las etiquetas de
los puntos se componen en JavaScript y las marcas de tiempo viajan como minutos
desde la época. Con cuatro pares de ocho años de M15 la diferencia entre hacerlo
así y mandar el texto ya montado son cientos de megabytes.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.offline as pyo

from chronos.application.chart.config import (
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

#: Los tonos de lo que dibuja el PROPIETARIO a mano encima del gráfico. Van a
#: hues bien separados (318°, 190° y 70°) para poder distinguirse entre ellos, y
#: quedan fuera de la serie categórica y del violeta a propósito: el día que
#: haya capas calculadas, lo que se vea con estos tres colores seguirá siendo lo
#: que ha puesto una mano.
HAND_COLORS: tuple[str, ...] = (theme.MAGENTA, theme.CYAN, theme.OLIVE)

#: Minuto cero de la escala de tiempos del explorador.
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def render_explorer(
    run: ChartRun,
    generated_at: datetime | None = None,
    account: dict[str, Any] | None = None,
) -> str:
    """Devuelve el HTML completo del explorador."""
    generated_at = generated_at or SystemClock().now()
    payload = build_payload(run, account)
    # El JSON viaja dentro de un <script>: escapar `</` evita que un texto
    # cualquiera pueda cerrar la etiqueta antes de tiempo.
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).replace(
        "</", "<\\/"
    )
    names = " · ".join(item.name for item in run.symbols)
    replacements = {
        "__TITLE__": f"Explorador de velas · {names}",
        "__HEADER__": f"Explorador de velas · {names}",
        "__SUBTITLE__": _subtitle(run),
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
    run: ChartRun, account: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Serializa la corrida a la estructura que consume el explorador."""
    config = run.config
    payload: dict[str, Any] = {
        "meta": {
            "priceSide": config.price_side,
            "sessionTimezone": config.reporting.session_timezone,
            "sessionTimezoneLabel": session_label(config.reporting.session_timezone),
            "h4OffsetHours": config.aggregation.h4_offset_hours,
            "dSessionStart": config.aggregation.d_session_start,
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
        "symbols": [
            _symbol_payload(item, config.reporting.max_explorer_bars)
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


def _symbol_payload(item: SymbolBars, max_bars: int) -> dict[str, Any]:
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
    }


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


def _subtitle(run: ChartRun) -> str:
    config = run.config
    pares = ", ".join(
        f"{item.name} ({', '.join(TIMEFRAME_LABELS.get(tf, tf) for tf in item.timeframes)})"
        for item in run.symbols
    )
    faltan = f" · sin embeber: {', '.join(run.unavailable)}" if run.unavailable else ""
    return (
        f"{pares} · lado {config.price_side} · día desde "
        f"{config.aggregation.describe_daily_start()} · sin estrategia: sólo velas y lo "
        f"que marques a mano{faltan}"
    )


def payload_size(payload: dict[str, Any]) -> int:
    """Bytes del JSON embebido. Útil para vigilar que el fichero no se dispare."""
    return len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))


def bar_counts(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        symbol["id"]: {chart: len(bars["t"]) for chart, bars in symbol["bars"].items()}
        for symbol in payload["symbols"]
    }
