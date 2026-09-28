"""La caja de las 02:00 de Nueva York, en H3.

Cada día abre una vela de H3 a las 02:00 NY. Un minuto antes —al cierre de la
vela anterior— se marca una caja:

- si hay un rango CRT vivo en H3 (``ranges.py``), la caja es ese rango: su vela 1.
  En H3 un rango sólo muere al tocar su objetivo o cerrar más allá de su otro
  extremo; el turtle soup a la vela anterior (``rechazo``) es del Diario y aquí
  no cuenta. La vela que termina un rango no hace nacer otro en H3;
- si no lo hay, la caja es la vela anterior, sea o no un rango. Si la vela que
  terminó el rango es la anterior, la caja es ella.

Si la caja es un rango, se espera a que termine (toca su objetivo o cierra más
allá del otro extremo, como en ``ranges.py``), en la vela de las 02:00 o en una
posterior: al cierre, la vela que lo termina pasa a ser la caja (``fin_rango``).
Mientras el rango vive no le aplica nada más. Si no termina antes de las 12:00,
la caja del día es el rango.

Desde que la caja es una vela, vela a vela, puede quedar inhabilitada y
sustituida por la vela que la inhabilita, al cierre de esa vela:

- ``ruptura``: la vela de las 02:00 CIERRA fuera de la caja, por arriba o por
  abajo. Si sólo saca la mecha y cierra dentro (rechazo), la caja se mantiene.
- ``barrido``: el precio toca un extremo de la caja y luego el otro, en la misma
  vela o en velas distintas. La caja pasa a ser la vela que toca el segundo.
  Después de las 02:00, cerrar fuera NO la inhabilita: sólo tocar los dos.

La caja vale hasta las 12:00 NY, el final de la ventana de entradas: sólo
cuentan las velas que cierran a esa hora o antes.

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
SWEEP = "barrido"               # la vela que tocó el segundo extremo de la caja
RANGE_END = "fin_rango"         # la vela que terminó el rango vivo a las 02:00

COLUMNS = ("kind", "direction", "reference", "known", "high", "low", "replaced", "until")


@dataclass(frozen=True, slots=True)
class DecisionSchedule:
    """El horario del día, en la hora de pared de ``timezone``: la caja, la
    ventana de entradas y la vida máxima de una posición."""

    timezone: str = "America/New_York"
    decision: time = time(2, 0)
    window_end: time = time(12, 0)
    position_end: time = time(16, 30)


NEW_YORK = DecisionSchedule()


def decision_boxes(bars: pd.DataFrame, schedule: DecisionSchedule = NEW_YORK) -> pd.DataFrame:
    """Las cajas de cada día, en orden: la de antes de las 02:00 y las que la
    van sustituyendo hasta las 12:00.

    Posiciones enteras sobre ``bars``: ``reference`` es la vela que da el máximo
    y el mínimo de la caja y ``known`` la vela a cuyo cierre existe la caja.
    ``direction`` es la del rango (0 si la caja es una vela). ``replaced`` dice
    si otra caja la sustituyó ese día y ``until`` es el instante UTC de las
    12:00 de ese día.

    La primera caja del día sale de la vela que CIERRA a las 02:00, así que
    existe aunque la de las 02:00 todavía no esté en la serie.
    """
    if len(bars) < 2:
        return pd.DataFrame(columns=list(COLUMNS)).astype(_DTYPES)
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    index = pd.DatetimeIndex(bars.index)
    # Duración de la vela: la mediana de los saltos, que los huecos de fin de
    # semana no mueven.
    span = pd.Timedelta(pd.Series(index).diff().median())
    closes_local = (index + span).tz_convert(schedule.timezone)
    at_decision = (closes_local.hour == schedule.decision.hour) & (
        closes_local.minute == schedule.decision.minute
    )
    prior = np.flatnonzero(at_decision)
    if not len(prior):
        return pd.DataFrame(columns=list(COLUMNS)).astype(_DTYPES)

    # Sólo hay un rango vivo a la vez y van en orden: el único candidato es el
    # último confirmado a más tardar en la vela anterior.
    ranges = crt_ranges(bars, rejection=False, birth_on_end=False)
    alive = np.zeros(len(prior), dtype=bool)
    slot = np.zeros(len(prior), dtype=int)
    if len(ranges):
        confirmation = ranges["confirmation"].to_numpy(dtype=int)
        end = ranges["end"].to_numpy(dtype=int)
        last = np.searchsorted(confirmation, prior, side="right") - 1
        slot = np.maximum(last, 0)
        alive = (last >= 0) & ((end[slot] == -1) | (end[slot] > prior))

    def pick(from_range: str, from_bar: np.ndarray) -> np.ndarray:
        return np.where(alive, ranges[from_range].to_numpy()[slot], from_bar) if len(ranges) else from_bar

    first_high = pick("high", high[prior]).astype(float)
    first_low = pick("low", low[prior]).astype(float)
    first_reference = pick("reference", prior).astype(int)
    first_direction = pick("direction", np.zeros(len(prior), dtype=int)).astype(int)
    range_end = pick("end", np.full(len(prior), -1)).astype(int)

    day = closes_local[prior].tz_localize(None).normalize()
    window_end = pd.Timedelta(hours=schedule.window_end.hour, minutes=schedule.window_end.minute)
    until = (day + window_end).tz_localize(schedule.timezone).tz_convert("UTC")
    # Última vela de cada día que cierra dentro de la ventana.
    last_in_window = index.searchsorted(until - span, side="right") - 1

    # Recorrido de estado: cada caja depende de cuáles tocó la anterior. Son
    # tres o cuatro velas por día sobre una serie ya agregada, no el hot path.
    opening = index[prior] + span   # la etiqueta de la vela de las 02:00
    rows: list[tuple[object, ...]] = []
    for day_number, before in enumerate(prior):
        box: tuple[str, int, int, int, float, float] = (
            RANGE if alive[day_number] else PREVIOUS_BAR,
            int(first_direction[day_number]),
            int(first_reference[day_number]),
            int(before),
            float(first_high[day_number]),
            float(first_low[day_number]),
        )
        touched_high = touched_low = False
        for bar in range(before + 1, int(last_in_window[day_number]) + 1):
            if box[0] == RANGE:
                if bar != range_end[day_number]:
                    continue
                kind = RANGE_END
                rows.append((*box, True, until[day_number]))
                box = (kind, 0, bar, bar, float(high[bar]), float(low[bar]))
                continue
            box_high, box_low = box[4], box[5]
            opens_at_decision = bool(index[bar] == opening[day_number])
            if opens_at_decision and (close[bar] > box_high or close[bar] < box_low):
                kind = BREAKOUT
            else:
                touched_high = touched_high or high[bar] >= box_high
                touched_low = touched_low or low[bar] <= box_low
                if not (touched_high and touched_low):
                    continue
                kind = SWEEP
            rows.append((*box, True, until[day_number]))
            box = (kind, 0, bar, bar, float(high[bar]), float(low[bar]))
            touched_high = touched_low = False
        rows.append((*box, False, until[day_number]))
    return pd.DataFrame(rows, columns=list(COLUMNS)).astype(_DTYPES)


_DTYPES: dict[Hashable, Any] = {
    "kind": str,
    "direction": int,
    "reference": int,
    "known": int,
    "high": float,
    "low": float,
    "replaced": bool,
    "until": "datetime64[ns, UTC]",
}
