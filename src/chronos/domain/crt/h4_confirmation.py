"""La confirmación en H4 de la señal de H1: la que da paso a buscar entradas.

Con la señal de H1 activa (``confirmation.py``) se sube a H4 y se mira la vela
de H4 EN CURSO, armada con las velas de H1 que ya han cerrado:

- tiene que estar haciendo **turtle soup** a la vela de H4 INMEDIATAMENTE
  ANTERIOR, del mismo lado que la señal de H1: si la de H1 barrió un mínimo, la
  de H4 en curso ya le ha sacado el mínimo a la anterior y el último cierre de
  H1 está de vuelta por encima de él; el espejo si barrió un máximo. Barrer una
  vela de H4 de dos o más atrás no cuenta, y la que le ha sacado los dos
  extremos a la anterior es ambigua y no confirma.
- se mira al cierre de la vela de H1 de la señal y, si ahí no, en los cierres
  de H1 siguientes mientras la señal siga activa: hasta que cierre su vela de
  H3 y nunca después de las 12:00 NY. Vale el primero.

Como la de H1, no necesita la vela en curso en la serie de H4: sale del cierre
de la última vela de H4 ya cerrada, así que vale igual con sólo las cerradas.
Agnóstico a par: precio contra precio, sin umbrales.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

import numpy as np
import pandas as pd

from chronos.domain.crt.confirmation import confirmation_signals
from chronos.domain.crt.decision_box import NEW_YORK, DecisionSchedule, decision_boxes
from chronos.domain.crt.ranges import BULLISH

COLUMNS = ("direction", "signal", "bar", "previous", "swept", "opposite", "h4_open", "h4_close")


def h4_confirmations(
    h1: pd.DataFrame, h3: pd.DataFrame, h4: pd.DataFrame, schedule: DecisionSchedule = NEW_YORK
) -> pd.DataFrame:
    """Las señales de H1 que H4 confirma, una fila por cada una, en orden.

    ``signal`` es la fila de la señal en ``confirmation_signals(h1, h3)`` y
    ``direction`` la suya. ``bar`` es la vela de H1 a cuyo cierre confirma H4,
    ``previous`` la posición sobre ``h4`` de la vela anterior a la de H4 en
    curso, ``swept`` su extremo barrido y ``opposite`` el otro.
    ``h4_open``/``h4_close`` es la vela de H4 en curso.
    """
    empty = pd.DataFrame(columns=list(COLUMNS)).astype(_DTYPES)
    signals = confirmation_signals(h1, h3, schedule)
    if signals.empty or len(h4) < 2:
        return empty

    h1_index = pd.DatetimeIndex(h1.index)
    h1_span = pd.Timedelta(pd.Series(h1_index).diff().median())
    h4_index = pd.DatetimeIndex(h4.index)
    h4_span = pd.Timedelta(pd.Series(h4_index).diff().median())

    # La última vela de H4 cerrada al abrir cada vela de H1, y la de H4 en curso
    # justo después. Si entre las dos falta una vela (un hueco), no hay «anterior».
    previous = h4_index.searchsorted(h1_index - h4_span, side="right") - 1
    safe_previous = np.maximum(previous, 0)
    h4_open = h4_index[safe_previous] + h4_span
    valid = (previous >= 0) & (h1_index < h4_open + h4_span)

    high = h1["high"].to_numpy(dtype=float)
    low = h1["low"].to_numpy(dtype=float)
    close = h1["close"].to_numpy(dtype=float)
    group = np.where(valid, h4_open.to_numpy(dtype="datetime64[ns]").view("int64"), -1)
    high_so_far = pd.Series(high).groupby(group).cummax().to_numpy()
    low_so_far = pd.Series(low).groupby(group).cummin().to_numpy()
    previous_high = h4["high"].to_numpy(dtype=float)[safe_previous]
    previous_low = h4["low"].to_numpy(dtype=float)[safe_previous]
    took_high = high_so_far > previous_high
    took_low = low_so_far < previous_low
    bullish_soup = valid & took_low & ~took_high & (close > previous_low)
    bearish_soup = valid & took_high & ~took_low & (close < previous_high)

    # Hasta cuándo sigue activa cada señal: el cierre de su vela de H3, sin
    # pasar de las 12:00 NY.
    until = pd.DatetimeIndex(decision_boxes(h3, schedule)["until"])[signals["box"].to_numpy()]
    limit = np.minimum(
        pd.DatetimeIndex(signals["h3_close"]).to_numpy(), until.to_numpy()
    )
    signal_bar = signals["bar"].to_numpy(dtype=int)
    bullish = signals["direction"].to_numpy(dtype=int) == BULLISH
    first = np.where(
        bullish,
        _first_from(np.flatnonzero(bullish_soup), signal_bar),
        _first_from(np.flatnonzero(bearish_soup), signal_bar),
    )
    found = first >= 0
    safe_first = np.maximum(first, 0)
    found &= (h1_index[safe_first] + h1_span).to_numpy() <= limit
    rows = np.flatnonzero(found)
    if not len(rows):
        return empty
    bars = first[rows]
    return pd.DataFrame(
        {
            "direction": signals["direction"].to_numpy(dtype=int)[rows],
            "signal": rows,
            "bar": bars,
            "previous": safe_previous[bars],
            "swept": np.where(bullish[rows], previous_low[bars], previous_high[bars]),
            "opposite": np.where(bullish[rows], previous_high[bars], previous_low[bars]),
            "h4_open": h4_open[bars],
            "h4_close": h4_open[bars] + h4_span,
        }
    ).astype(_DTYPES)


def _first_from(candidates: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """El primer candidato en o después de cada inicio; -1 si no hay."""
    position = np.searchsorted(candidates, starts, side="left")
    return np.where(
        position < len(candidates), candidates[np.minimum(position, len(candidates) - 1)], -1
    ) if len(candidates) else np.full(len(starts), -1)


_DTYPES: dict[Hashable, Any] = {
    "direction": int,
    "signal": int,
    "bar": int,
    "previous": int,
    "swept": float,
    "opposite": float,
    "h4_open": "datetime64[ns, UTC]",
    "h4_close": "datetime64[ns, UTC]",
}
