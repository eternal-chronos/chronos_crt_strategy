"""La caja de las 02:00 de Nueva York, en H3.

Cada día abre una vela de H3 a las 02:00 NY. Un minuto antes —al cierre de la
vela anterior— se marca una caja:

- si hay un rango CRT vivo en H3 (``ranges.py``), la caja es ese rango: su vela 1;
- si no lo hay, la caja es la vela anterior, sea o no un rango.

Al cerrar la vela de las 02:00: si CIERRA fuera de la caja, por arriba o por
abajo, esa vela pasa a ser la caja. Si se sale con la mecha y cierra dentro
(rechazo), o ni se sale, la caja se mantiene.

La caja vale hasta las 12:00 NY, el final de la ventana de entradas.

La hora se decide con el reloj de Nueva York, con su horario de verano: en UTC
la vela de las 02:00 se mueve una hora dos veces al año. Agnóstico a par: se
compara precio con precio y no hay ni un umbral.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from datetime import time
from typing import Any

import numpy as np
import pandas as pd

from chronos.domain.crt.ranges import crt_ranges

#: De dónde sale la caja.
RANGE = "rango"                 # el rango CRT vivo antes de las 02:00
PREVIOUS_BAR = "vela_previa"    # sin rango vivo: la vela anterior a las 02:00
BREAKOUT = "ruptura"            # la vela de las 02:00, que cerró fuera de la caja

COLUMNS = ("kind", "direction", "reference", "known", "decision", "high", "low", "replaced", "until")


@dataclass(frozen=True, slots=True)
class DecisionSchedule:
    """El horario de la caja, en la hora de pared de ``timezone``."""

    timezone: str = "America/New_York"
    decision: time = time(2, 0)
    window_end: time = time(12, 0)


NEW_YORK = DecisionSchedule()


def decision_boxes(bars: pd.DataFrame, schedule: DecisionSchedule = NEW_YORK) -> pd.DataFrame:
    """Las cajas de cada día, en orden: la de antes de las 02:00 y, si esa vela
    cerró fuera, la que la sustituye.

    Posiciones enteras sobre ``bars``: ``reference`` es la vela que da el máximo
    y el mínimo de la caja, ``known`` la vela a cuyo cierre existe la caja y
    ``decision`` la vela de las 02:00. ``direction`` es la del rango (0 si la
    caja es una vela). ``replaced`` dice si la vela de las 02:00 la sustituyó y
    ``until`` es el instante UTC de las 12:00 de ese día.
    """
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    local = pd.DatetimeIndex(bars.index).tz_convert(schedule.timezone)
    at_decision = (local.hour == schedule.decision.hour) & (local.minute == schedule.decision.minute)
    decision = np.flatnonzero(at_decision)
    decision = decision[decision >= 1]
    if not len(decision):
        return pd.DataFrame(columns=list(COLUMNS)).astype(_DTYPES)
    prior = decision - 1

    # Sólo hay un rango vivo a la vez y van en orden: el único candidato es el
    # último confirmado a más tardar en la vela anterior.
    ranges = crt_ranges(bars)
    alive = np.zeros(len(decision), dtype=bool)
    slot = np.zeros(len(decision), dtype=int)
    if len(ranges):
        confirmation = ranges["confirmation"].to_numpy(dtype=int)
        end = ranges["end"].to_numpy(dtype=int)
        last = np.searchsorted(confirmation, prior, side="right") - 1
        slot = np.maximum(last, 0)
        alive = (last >= 0) & ((end[slot] == -1) | (end[slot] > prior))

    def pick(from_range: str, from_bar: np.ndarray) -> np.ndarray:
        return np.where(alive, ranges[from_range].to_numpy()[slot], from_bar) if len(ranges) else from_bar

    box_high = pick("high", high[prior]).astype(float)
    box_low = pick("low", low[prior]).astype(float)
    broken = (close[decision] > box_high) | (close[decision] < box_low)

    day = local[decision].tz_localize(None).normalize()
    window_end = pd.Timedelta(hours=schedule.window_end.hour, minutes=schedule.window_end.minute)
    until = (day + window_end).tz_localize(schedule.timezone).tz_convert("UTC")

    first = pd.DataFrame({
        "kind": np.where(alive, RANGE, PREVIOUS_BAR),
        "direction": pick("direction", np.zeros(len(decision), dtype=int)).astype(int),
        "reference": pick("reference", prior).astype(int),
        "known": prior,
        "decision": decision,
        "high": box_high,
        "low": box_low,
        "replaced": broken,
        "until": until,
    })
    breakout = pd.DataFrame({
        "kind": BREAKOUT,
        "direction": 0,
        "reference": decision,
        "known": decision,
        "decision": decision,
        "high": high[decision],
        "low": low[decision],
        "replaced": False,
        "until": until,
    })[broken]
    boxes = pd.concat([first, breakout], ignore_index=True)
    boxes = boxes.sort_values(["decision", "known"], kind="stable", ignore_index=True)
    return boxes[list(COLUMNS)].astype(_DTYPES)


_DTYPES: dict[Hashable, Any] = {
    "kind": str,
    "direction": int,
    "reference": int,
    "known": int,
    "decision": int,
    "high": float,
    "low": float,
    "replaced": bool,
    "until": "datetime64[ns, UTC]",
}
