"""Rangos CRT y cuándo dejan de valer.

**Nace** un rango cuando la vela 2 le saca UN extremo a la vela 1 y cierra dentro
de ella. Si saca el máximo, el rango es bajista; si saca el mínimo, alcista. El
rango es la vela 1 entera, de su mínimo a su máximo. La vela que saca los dos
extremos no abre nada (es ambigua) y la que cierra fuera tampoco (es expansión).

**Vive** mientras nada lo frene, y lo frenan tres cosas, contadas desde la vela
siguiente a la que lo confirmó. Se habla del bajista; el alcista es el espejo:

- ``rechazo``: una vela le saca el mínimo a la vela ANTERIOR (a la que sea, no a
  la vela 1) y cierra por encima de ese mínimo. Es el turtle soup o el rango
  alcista que se forma dentro. Romper el mínimo y cerrar por debajo NO lo frena:
  eso es que sigue bajando. **Es regla del Diario**: con ``rejection=False``
  (H3) no frena nada y el rango sólo muere por las otras dos.
- ``cierre_fuera``: una vela cierra por encima del máximo de la vela 1.
- ``objetivo``: una vela toca el mínimo de la vela 1.

Si en la misma vela se dan varias, se apunta la primera de esa lista en el orden
``cierre_fuera``, ``rechazo``, ``objetivo``: lo que manda es que el rango muere.

Sólo hay un rango vivo a la vez. Un rango del mismo sentido que se forme mientras
otro vive se ignora. Con ``rechazo``, uno del sentido contrario no puede formarse
sin frenar al vivo (le saca el extremo a la vela anterior y cierra de vuelta
dentro), así que en esa misma vela muere el viejo y nace el nuevo. Sin él, el
contrario también se ignora mientras el vivo no muera.

Agnóstico a par y a temporalidad: se compara precio con precio de la misma serie
y no hay ni un umbral.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BEARISH = -1
BULLISH = 1

#: Por qué murió el rango. Vacío si sigue vivo en la última vela.
CLOSED_BEYOND = "cierre_fuera"
REJECTED = "rechazo"
TARGET_HIT = "objetivo"

COLUMNS = ("direction", "reference", "confirmation", "high", "low", "end", "end_reason")


def crt_ranges(bars: pd.DataFrame, *, rejection: bool = True) -> pd.DataFrame:
    """Los rangos CRT de la serie, en orden, con su final.

    Devuelve una fila por rango con posiciones enteras sobre ``bars``:
    ``reference`` (vela 1), ``confirmation`` (vela 2, la que lo hace existir al
    cerrar), ``end`` (la vela que lo frenó, -1 si sigue vivo) y ``end_reason``.
    ``high``/``low`` son los de la vela 1. ``rejection`` dice si el turtle soup a
    la vela anterior frena el rango: sí en el Diario, no en H3.

    El nacimiento y los frenos que sólo miran la vela anterior se calculan
    vectorizados; lo que queda es un recorrido de estado —un rango vivo o
    ninguno— que por definición depende de la vela anterior del recorrido. Va
    sobre una serie ya agregada (unas 250 velas por año en el diario), no sobre
    el hot path de ticks.
    """
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    size = len(high)
    if size < 2:
        return pd.DataFrame(columns=list(COLUMNS)).astype(_DTYPES)

    prev_high = np.r_[np.nan, high[:-1]]
    prev_low = np.r_[np.nan, low[:-1]]
    took_high = high > prev_high
    took_low = low < prev_low
    closed_inside = (close < prev_high) & (close > prev_low)

    births = np.zeros(size, dtype=int)
    births[took_high & ~took_low & closed_inside] = BEARISH
    births[took_low & ~took_high & closed_inside] = BULLISH
    # Le saca el extremo a la vela anterior y cierra de vuelta del otro lado de él.
    rejects_bearish = took_low & (close > prev_low) & rejection
    rejects_bullish = took_high & (close < prev_high) & rejection

    rows: list[_Range] = []
    alive: _Range | None = None
    for index in range(1, size):
        if alive is not None:
            rejected = rejects_bearish[index] if alive.direction == BEARISH else rejects_bullish[index]
            reason = alive.end_reason_at(close[index], high[index], low[index], bool(rejected))
            if reason:
                alive.end = index
                alive.end_reason = reason
                alive = None
        if alive is None and births[index]:
            alive = _Range(
                direction=int(births[index]),
                reference=index - 1,
                confirmation=index,
                high=float(high[index - 1]),
                low=float(low[index - 1]),
            )
            rows.append(alive)
    return pd.DataFrame(
        [[getattr(row, column) for column in COLUMNS] for row in rows], columns=list(COLUMNS)
    ).astype(_DTYPES)


@dataclass(slots=True)
class _Range:
    """Un rango mientras se recorre la serie. Mutable sólo para apuntar su final."""

    direction: int
    reference: int
    confirmation: int
    high: float
    low: float
    end: int = -1
    end_reason: str = ""

    def end_reason_at(self, close: float, high: float, low: float, rejected: bool) -> str:
        """Por qué lo frena esta vela, o vacío si sigue vivo."""
        if self.direction == BEARISH:
            closed_beyond, target_hit = close > self.high, low <= self.low
        else:
            closed_beyond, target_hit = close < self.low, high >= self.high
        if closed_beyond:
            return CLOSED_BEYOND
        if rejected:
            return REJECTED
        if target_hit:
            return TARGET_HIT
        return ""


_DTYPES = {
    "direction": int,
    "reference": int,
    "confirmation": int,
    "high": float,
    "low": float,
    "end": int,
    "end_reason": str,
}
