"""La señal de confirmación en H1, sobre la caja de las 02:00 de H3.

Mientras una vela de H3 está abierta, sus velas de H1 se miran contra la caja
de las 02:00 vigente al abrir esa vela de H3 (``decision_box.py``):

1. H1 **toca** un extremo de la caja, con la mecha basta.
2. Después —o en la misma vela— una vela de H1 hace **turtle soup** en ese lado:
   le saca el extremo a la vela de H1 INMEDIATAMENTE ANTERIOR y cierra de vuelta
   del otro lado de él. Si tocó el mínimo de la caja, le saca el mínimo a la
   anterior y cierra por encima (alcista); si tocó el máximo, el espejo. Que
   además cierre dentro de la vela anterior (rango CRT) o por encima de ella da
   igual. Barrer una vela de dos o más atrás no cuenta.

Esa vela activa la señal: anticipa que la vela de H3 cerrará de vuelta dentro de
la caja. Da igual que ocurra fuera de la caja. Se apuntan las dos líneas de la
vela anterior: la del turtle soup (el extremo barrido) y la del otro extremo.

Cada vela de H3 empieza de cero: el toque de una vela de H3 no vale para la
siguiente, y sólo cuenta la primera señal de cada vela de H3. La caja la cambia
``decision_box.py`` al cierre de H3 (ruptura o barrido); la de la vela
siguiente es la nueva. Sólo cuentan las velas de H1 que cierran a las 12:00 NY
o antes, como la caja.

La vela que barre la anterior por los DOS lados es ambigua y no da señal.

Agnóstico a par: se compara precio con precio y no hay ni un umbral. Tampoco
necesita que la vela de H3 en curso esté en la serie de H3: la rejilla de H3 se
cuenta desde el cierre de la vela que hizo la caja, así que vale igual con sólo
las velas de H3 ya cerradas.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

import numpy as np
import pandas as pd

from chronos.domain.crt.decision_box import NEW_YORK, DecisionSchedule, decision_boxes
from chronos.domain.crt.ranges import BEARISH, BULLISH

COLUMNS = ("direction", "bar", "swept", "opposite", "box", "h3_open", "h3_close")


def confirmation_signals(
    h1: pd.DataFrame, h3: pd.DataFrame, schedule: DecisionSchedule = NEW_YORK
) -> pd.DataFrame:
    """Las señales de confirmación, una como mucho por vela de H3, en orden.

    ``direction`` es la del turtle soup (``BULLISH`` si barrió el mínimo),
    ``bar`` la posición de su vela sobre ``h1`` (la vela anterior es
    ``bar - 1``), ``swept`` el extremo barrido de la vela anterior y
    ``opposite`` su otro extremo. ``box`` es la posición de la caja en
    ``decision_boxes(h3)``; ``h3_open``/``h3_close``, la vela de H3 en curso.
    """
    empty = pd.DataFrame(columns=list(COLUMNS)).astype(_DTYPES)
    if len(h1) < 2 or len(h3) < 2:
        return empty
    boxes = decision_boxes(h3, schedule)
    if boxes.empty:
        return empty

    h3_index = pd.DatetimeIndex(h3.index)
    h3_span = pd.Timedelta(pd.Series(h3_index).diff().median())
    h1_index = pd.DatetimeIndex(h1.index)
    h1_span = pd.Timedelta(pd.Series(h1_index).diff().median())

    # Cada caja existe desde el cierre de la vela que la hizo, que es un borde
    # de la rejilla de H3.
    box_start = h3_index[boxes["known"].to_numpy(dtype=int)] + h3_span
    box_until = pd.DatetimeIndex(boxes["until"])
    slot = box_start.searchsorted(h1_index, side="right") - 1
    valid = slot >= 0
    safe_slot = np.maximum(slot, 0)
    valid &= h1_index + h1_span <= box_until[safe_slot]

    # La vela de H3 en curso, contada desde el arranque de la caja: dentro de
    # un mismo día no hay cambio de hora, así que la rejilla es regular.
    start = box_start[safe_slot]
    h3_open = start + ((h1_index - start) // h3_span) * h3_span

    box_high = boxes["high"].to_numpy(dtype=float)[safe_slot]
    box_low = boxes["low"].to_numpy(dtype=float)[safe_slot]
    high = h1["high"].to_numpy(dtype=float)
    low = h1["low"].to_numpy(dtype=float)
    close = h1["close"].to_numpy(dtype=float)

    # Lo tocado se acumula dentro de cada vela de H3, desde su primera de H1.
    # Las velas sin caja van todas a un grupo de descarte que ``valid`` anula.
    group = np.where(valid, h3_open.to_numpy(dtype="datetime64[ns]").view("int64"), -1)
    touched_high = pd.Series(high, index=h1_index).groupby(group).cummax().reindex(h1_index)
    touched_low = pd.Series(low, index=h1_index).groupby(group).cummin().reindex(h1_index)
    touched_high_box = (touched_high.to_numpy() >= box_high) & valid
    touched_low_box = (touched_low.to_numpy() <= box_low) & valid

    prev_high = np.r_[np.nan, high[:-1]]
    prev_low = np.r_[np.nan, low[:-1]]
    took_high = high > prev_high
    took_low = low < prev_low
    bullish_soup = took_low & ~took_high & (close > prev_low) & touched_low_box
    bearish_soup = took_high & ~took_low & (close < prev_high) & touched_high_box

    signal = bullish_soup | bearish_soup
    bars = np.flatnonzero(signal)
    if not len(bars):
        return empty
    frame = pd.DataFrame(
        {
            "direction": np.where(bullish_soup[bars], BULLISH, BEARISH),
            "bar": bars,
            "swept": np.where(bullish_soup[bars], prev_low[bars], prev_high[bars]),
            "opposite": np.where(bullish_soup[bars], prev_high[bars], prev_low[bars]),
            "box": safe_slot[bars],
            "h3_open": h3_open[bars],
            "h3_close": h3_open[bars] + h3_span,
        }
    )
    return frame.drop_duplicates("h3_open").reset_index(drop=True).astype(_DTYPES)


_DTYPES: dict[Hashable, Any] = {
    "direction": int,
    "bar": int,
    "swept": float,
    "opposite": float,
    "box": int,
    "h3_open": "datetime64[ns, UTC]",
    "h3_close": "datetime64[ns, UTC]",
}
