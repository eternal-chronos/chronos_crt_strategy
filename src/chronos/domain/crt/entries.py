"""Las entradas: cuando H3, H1 y H4 se cumplen, se baja a 15M y se entra.

Con la confirmación de H4 (``h4_confirmation.py``), que se sabe al cierre de una
vela de H1, se espera a que cierre la vela de 15M en curso —la que abre en ese
mismo instante— y se entra al precio de su cierre, en el sentido de la señal de
H1 (vende si barrió el máximo, compra si barrió el mínimo):

- **stop**: el extremo del turtle soup. En venta, el máximo más alto que ha
  hecho el precio desde que abrió la vela de H1 de la señal hasta el cierre de
  la vela de 15M de la entrada; en compra, el mínimo más bajo.
- **take**: el objetivo del rango de H3, el lado contrario de la caja de las
  02:00 contra la que saltó la señal: en venta su mínimo, en compra su máximo.
  Aunque después nazca una caja nueva, el take de esta operación no se mueve.

La entrada sólo vale si su vela de 15M cierra dentro de la ventana (a las 12:00
NY o antes) y si el precio de entrada queda entre el stop y el take: si ya ha
llegado al take, o no queda distancia al stop, no hay operación. Como mucho
``EntryRules.max_per_day`` al día, las primeras.

Después, vela a vela de 15M, sale en la primera que toca el stop (pierde 1 R) o
el take (gana la distancia al take entre la distancia al stop). Si una misma
vela toca los dos, cuenta el stop: dentro de la vela no se sabe el orden. Si a
las 16:30 NY no ha tocado ninguno, se cierra al cierre de la vela de 15M que
cierra a esa hora. Si la serie se acaba antes, la operación sigue abierta.

Agnóstico a par: precio contra precio, sin umbrales.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from chronos.domain.crt.confirmation import confirmation_signals
from chronos.domain.crt.decision_box import NEW_YORK, DecisionSchedule, decision_boxes
from chronos.domain.crt.h4_confirmation import h4_confirmations
from chronos.domain.crt.ranges import BEARISH, BULLISH

#: Por qué salió la operación. Vacío si sigue abierta al final de la serie.
STOP = "stop"
TAKE = "take"
TIME = "cierre_16:30"

COLUMNS = (
    "direction", "confirmation", "bar", "entry", "stop", "take",
    "exit_bar", "exit", "exit_reason", "r",
)


@dataclass(frozen=True, slots=True)
class EntryRules:
    """Cuántas operaciones se abren como mucho cada día."""

    max_per_day: int = 2


ENTRY_RULES = EntryRules()


def entries(
    h1: pd.DataFrame,
    h3: pd.DataFrame,
    h4: pd.DataFrame,
    m15: pd.DataFrame,
    schedule: DecisionSchedule = NEW_YORK,
    rules: EntryRules = ENTRY_RULES,
) -> pd.DataFrame:
    """Las operaciones, en orden de entrada.

    ``confirmation`` es la fila en ``h4_confirmations(h1, h3, h4)``. ``bar`` y
    ``exit_bar`` son posiciones sobre ``m15``: se entra al cierre de ``bar`` y se
    sale al cierre de ``exit_bar`` (-1 si sigue abierta). ``r`` es el resultado
    en múltiplos de riesgo (NaN si sigue abierta).
    """
    empty = pd.DataFrame(columns=list(COLUMNS)).astype(_DTYPES)
    confirmations = h4_confirmations(h1, h3, h4, schedule)
    if confirmations.empty or len(m15) < 2:
        return empty
    signals = confirmation_signals(h1, h3, schedule)
    boxes = decision_boxes(h3, schedule)

    h1_index = pd.DatetimeIndex(h1.index)
    h1_span = pd.Timedelta(pd.Series(h1_index).diff().median())
    m15_index = pd.DatetimeIndex(m15.index)
    m15_span = pd.Timedelta(pd.Series(m15_index).diff().median())
    high = m15["high"].to_numpy(dtype=float)
    low = m15["low"].to_numpy(dtype=float)
    close = m15["close"].to_numpy(dtype=float)

    signal_rows = confirmations["signal"].to_numpy(dtype=int)
    box_rows = signals["box"].to_numpy(dtype=int)[signal_rows]
    bearish = confirmations["direction"].to_numpy(dtype=int) == BEARISH
    take = np.where(
        bearish,
        boxes["low"].to_numpy(dtype=float)[box_rows],
        boxes["high"].to_numpy(dtype=float)[box_rows],
    )
    window_end = pd.DatetimeIndex(boxes["until"])[box_rows]
    day = window_end.tz_convert(schedule.timezone).tz_localize(None).normalize()
    position_end = (
        day + pd.Timedelta(hours=schedule.position_end.hour, minutes=schedule.position_end.minute)
    ).tz_localize(schedule.timezone).tz_convert("UTC")

    # La vela de 15M que abre cuando se sabe la confirmación, y la de la señal.
    known = h1_index[confirmations["bar"].to_numpy(dtype=int)] + h1_span
    bar = m15_index.searchsorted(known, side="left")
    signal_open = h1_index[signals["bar"].to_numpy(dtype=int)[signal_rows]]
    first = m15_index.searchsorted(signal_open, side="left")
    # Última vela de 15M que cierra a las 16:30 o antes.
    last = m15_index.searchsorted(position_end - m15_span, side="right") - 1
    data_end = m15_index[-1] + m15_span

    # Una fila por confirmación y como mucho dos por día: es un recorrido de
    # unas pocas operaciones por semana, no el hot path.
    rows: list[tuple[object, ...]] = []
    taken: dict[pd.Timestamp, int] = {}
    for row in range(len(confirmations)):
        entry_bar = int(bar[row])
        if entry_bar >= len(m15) or m15_index[entry_bar] + m15_span > window_end[row]:
            continue
        if taken.get(day[row], 0) >= rules.max_per_day:
            continue
        sell = bool(bearish[row])
        entry = float(close[entry_bar])
        swing = slice(int(first[row]), entry_bar + 1)
        stop = float(high[swing].max()) if sell else float(low[swing].min())
        target = float(take[row])
        if not ((target < entry < stop) if sell else (stop < entry < target)):
            continue
        taken[day[row]] = taken.get(day[row], 0) + 1

        exit_bar, exit_price, reason = -1, float("nan"), ""
        after = slice(entry_bar + 1, int(last[row]) + 1)
        hit_stop = high[after] >= stop if sell else low[after] <= stop
        hit_take = low[after] <= target if sell else high[after] >= target
        hits = np.flatnonzero(hit_stop | hit_take)
        if len(hits):
            exit_bar = entry_bar + 1 + int(hits[0])
            stopped = bool(hit_stop[hits[0]])
            exit_price, reason = (stop, STOP) if stopped else (target, TAKE)
        elif data_end >= position_end[row] and last[row] > entry_bar:
            exit_bar = int(last[row])
            exit_price, reason = float(close[exit_bar]), TIME
        risk = stop - entry if sell else entry - stop
        gained = entry - exit_price if sell else exit_price - entry
        rows.append((
            BEARISH if sell else BULLISH, row, entry_bar, entry, stop, target,
            exit_bar, exit_price, reason, -1.0 if reason == STOP else gained / risk,
        ))
    if not rows:
        return empty
    return pd.DataFrame(rows, columns=list(COLUMNS)).astype(_DTYPES)


_DTYPES: dict[Hashable, Any] = {
    "direction": int,
    "confirmation": int,
    "bar": int,
    "entry": float,
    "stop": float,
    "take": float,
    "exit_bar": int,
    "exit": float,
    "exit_reason": str,
    "r": float,
}
