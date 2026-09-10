"""Rangos CRT: la vela que toma un extremo de la anterior y cierra dentro de ella.

Es la primera regla de la estrategia y la única de este paso. Se lee sobre dos
velas consecutivas —`i-1` de referencia, `i` de confirmación— y produce un rango
con su objetivo y su invalidación:

    ALCISTA   low[i] < low[i-1]   y   low[i-1] < close[i] < high[i-1]
    BAJISTA   high[i] > high[i-1] y   low[i-1] < close[i] < high[i-1]

La vela que toma los DOS extremos y cierra dentro es ambigua y aquí no es rango:
no se sabe cuál de las dos manipulaciones manda. La que toma un extremo y cierra
FUERA por el otro lado tampoco: eso es expansión, y el cierre fuera la descarta
sola.

**El rango no existe hasta que la vela `i` cierra.** Todo lo que se devuelve va
etiquetado con la vela que lo supo: `t_confirm` es la marca de la vela de
confirmación y `t_resolved` la de la que lo resolvió, las dos al inicio de su
intervalo, que es la convención de barras del proyecto. Preguntar por el estado
«a fecha `as_of`» significa que esa vela YA CERRÓ; quien llame pasa la marca de
la última vela cerrada, nunca la de la que se está formando.

La función es agnóstica a la temporalidad —hoy sólo se la llama con el diario—
y no sabe de qué par son las velas: no hay ni un umbral en unidades de precio.
El único filtro de tamaño se mide en ATR de la propia temporalidad.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from chronos.domain.errors import DomainError
from chronos.domain.strategies.indicators import atr

#: Dirección del rango. Es la dirección del OBJETIVO, no la de la vela que lo
#: confirma: un rango alcista se confirma tomando el mínimo de la referencia.
BULLISH = "alcista"
BEARISH = "bajista"

#: Ciclo de vida. `activo` mientras no se toque ni el objetivo ni la
#: invalidación; `expirado` sólo existe con `max_candles_alive` puesto.
ACTIVE = "activo"
COMPLETED = "completado"
FAILED = "fallido"
EXPIRED = "expirado"

#: Lo que se contesta cuando no hay ningún rango vivo. No es «neutral» ni
#: «lateral»: es que no hay nada hacia lo que ir.
NO_TARGET = "SIN TARGET"

#: Periodo del ATR con el que se mide el tamaño del rango.
ATR_PERIOD = 14

#: Columnas del resultado, en orden, con el tipo con el que viajan. El de
#: `candles_to_resolve` es entero anulable a propósito: un rango vivo no lleva
#: cuenta de velas, y un cero ahí sería mentira.
_DTYPES: dict[str, Any] = {
    "range_id": "int64",
    "tf": "object",
    "direction": "object",
    "t_ref_open": "datetime64[ns, UTC]",
    "t_confirm": "datetime64[ns, UTC]",
    "range_high": "float64",
    "range_low": "float64",
    "manip_extreme": "float64",
    "target": "float64",
    "invalidation": "float64",
    "size": "float64",
    "size_atr": "float64",
    "status": "object",
    "t_resolved": "datetime64[ns, UTC]",
    "candles_to_resolve": "Int64",
    "ambiguous_resolution": "bool",
}

COLUMNS: tuple[str, ...] = tuple(_DTYPES)


@dataclass(frozen=True, slots=True)
class RangeParams:
    """Los dos parámetros del detector, con los valores con los que se mira hoy.

    `min_size_atr = 0` y `max_candles_alive = None` significan «no filtres
    nada»: el filtro de ruido está escrito pero apagado, porque primero se mira
    todo y después se decide qué sobra.
    """

    #: Tamaño mínimo del rango en ATR(14) de su temporalidad. 0 = sin filtro.
    min_size_atr: float = 0.0
    #: Velas que un rango puede seguir vivo antes de expirar. None = sin límite.
    max_candles_alive: int | None = None

    def __post_init__(self) -> None:
        if self.min_size_atr < 0:
            raise DomainError("min_size_atr no puede ser negativo")
        if self.max_candles_alive is not None and self.max_candles_alive < 1:
            raise DomainError("max_candles_alive tiene que ser al menos 1")

    def describe(self) -> str:
        tamano = (
            "sin filtro de tamaño"
            if self.min_size_atr <= 0
            else f"tamaño ≥ {self.min_size_atr:g} veces el ATR({ATR_PERIOD})"
        )
        vida = (
            "sin límite de vida"
            if self.max_candles_alive is None
            else f"expiran a las {self.max_candles_alive} velas"
        )
        return f"{tamano} · {vida}"


@dataclass(frozen=True, slots=True)
class RangeSummary:
    """Cuántos rangos hay y qué fue de ellos.

    Los porcentajes van sobre los RESUELTOS —completados, fallidos y
    expirados—, no sobre el total: un rango que todavía está vivo no ha fallado,
    y meterlo en el denominador hundiría el porcentaje de acierto sin que haya
    pasado nada. El total y los vivos van aparte para que no se esconda nada.
    """

    total: int
    bullish: int
    bearish: int
    completed: int
    failed: int
    expired: int
    #: Vivos a la fecha con la que se pidió el resumen.
    active: int
    resolved: int
    completed_pct: float | None
    failed_pct: float | None
    mean_candles_to_resolve: float | None


def detect_ranges(
    df: pd.DataFrame,
    tf: str,
    min_size_atr: float = 0.0,
    max_candles_alive: int | None = None,
) -> pd.DataFrame:
    """Detecta los rangos de `df` y resuelve su ciclo de vida vela a vela.

    `df` son las velas de UNA temporalidad en el formato canónico del proyecto:
    índice UTC monótono y columnas `open/high/low/close`. Devuelve un DataFrame
    con una fila por rango y las columnas de `COLUMNS`; sin rangos, el mismo
    DataFrame vacío con los mismos tipos.

    Ni una mirada al futuro: el estado de cada rango sale de las velas
    POSTERIORES a la que lo confirmó, y la fila entera se resuelve con el
    histórico que se le pase. Recortar ese histórico es lo que convierte esta
    misma función en «lo que se sabía aquel día».
    """
    params = RangeParams(min_size_atr=min_size_atr, max_candles_alive=max_candles_alive)
    index = pd.DatetimeIndex(df.index)
    if len(index) < 2:
        return empty_ranges()

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    confirm, bullish = _confirmations(high, low, close)
    reference = confirm - 1
    size = high[reference] - low[reference]
    size_atr = _size_in_atr(high, low, close, reference, size)

    if params.min_size_atr > 0:
        # `>=` con NaN da False: las primeras velas, sin ATR todavía, no pueden
        # pasar un filtro que no se les puede medir.
        keep = size_atr >= params.min_size_atr
        confirm, bullish, reference = confirm[keep], bullish[keep], reference[keep]
        size, size_atr = size[keep], size_atr[keep]

    if len(confirm) == 0:
        return empty_ranges()

    target = np.where(bullish, high[reference], low[reference])
    invalidation = np.where(bullish, low[confirm], high[confirm])

    status, resolved, ambiguous = _resolve(
        high, low, confirm, bullish, target, invalidation, params.max_candles_alive
    )

    resolved_at = np.where(
        resolved >= 0,
        index.to_numpy()[np.maximum(resolved, 0)],
        np.datetime64("NaT", "ns"),
    )
    candles = pd.array(np.where(resolved >= 0, resolved - confirm, 0), dtype="Int64")
    candles[resolved < 0] = pd.NA

    frame = pd.DataFrame(
        {
            "range_id": np.arange(1, len(confirm) + 1, dtype=np.int64),
            "tf": tf,
            "direction": np.where(bullish, BULLISH, BEARISH),
            "t_ref_open": index[reference],
            "t_confirm": index[confirm],
            "range_high": high[reference],
            "range_low": low[reference],
            "manip_extreme": np.where(bullish, low[confirm], high[confirm]),
            "target": target,
            "invalidation": invalidation,
            "size": size,
            "size_atr": size_atr,
            "status": status,
            "t_resolved": pd.DatetimeIndex(resolved_at, tz="UTC"),
            "candles_to_resolve": candles,
            "ambiguous_resolution": ambiguous,
        },
        index=pd.RangeIndex(len(confirm)),
    )
    return frame[list(COLUMNS)]


def empty_ranges() -> pd.DataFrame:
    """El resultado sin ningún rango, con los tipos de columna de siempre."""
    return pd.DataFrame({name: pd.Series(dtype=dtype) for name, dtype in _DTYPES.items()})


def current_bias(ranges_df: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, Any]:
    """Hacia dónde va el precio a fecha `as_of`, según los rangos que se sabían.

    Manda el rango vivo MÁS RECIENTE: el último que confirmó y todavía no ha
    tocado ni su objetivo ni su invalidación. Puede haber varios vivos a la vez
    —y en direcciones opuestas—; el sesgo es el del último porque es el que
    acaba de reescribir hacia dónde mira el mercado.

    `as_of` es la marca de una vela YA CERRADA. Sólo entran los rangos con
    `t_confirm <= as_of`, y sigue vivo el que no se resolvió o se resolvió
    después.
    """
    if ranges_df.empty:
        return {"direction": NO_TARGET}
    moment = _as_utc(as_of)
    known = ranges_df[ranges_df["t_confirm"] <= moment]
    if known.empty:
        return {"direction": NO_TARGET}
    resolved = known["t_resolved"]
    alive = known[resolved.isna() | (resolved > moment)]
    if alive.empty:
        return {"direction": NO_TARGET}
    last = alive.sort_values(["t_confirm", "range_id"]).iloc[-1]
    return {
        "direction": str(last["direction"]),
        "target": float(last["target"]),
        "invalidation": float(last["invalidation"]),
        "range_id": int(last["range_id"]),
    }


def summarize(ranges_df: pd.DataFrame, as_of: pd.Timestamp | None = None) -> RangeSummary:
    """Recuento de rangos y de lo que fue de ellos, con los vivos a `as_of`.

    Sin `as_of` los vivos son los que quedaron abiertos al final del histórico,
    que es lo mismo que decir «hoy» cuando el histórico llega hasta hoy.
    """
    if ranges_df.empty:
        return RangeSummary(0, 0, 0, 0, 0, 0, 0, 0, None, None, None)

    status = ranges_df["status"]
    completed = int((status == COMPLETED).sum())
    failed = int((status == FAILED).sum())
    expired = int((status == EXPIRED).sum())
    resolved = completed + failed + expired

    if as_of is None:
        active = int((status == ACTIVE).sum())
    else:
        moment = _as_utc(as_of)
        known = ranges_df[ranges_df["t_confirm"] <= moment]
        alive = known["t_resolved"].isna() | (known["t_resolved"] > moment)
        active = int(alive.sum())

    velas = ranges_df["candles_to_resolve"].dropna()
    return RangeSummary(
        total=len(ranges_df),
        bullish=int((ranges_df["direction"] == BULLISH).sum()),
        bearish=int((ranges_df["direction"] == BEARISH).sum()),
        completed=completed,
        failed=failed,
        expired=expired,
        active=active,
        resolved=resolved,
        completed_pct=100.0 * completed / resolved if resolved else None,
        failed_pct=100.0 * failed / resolved if resolved else None,
        mean_candles_to_resolve=float(velas.mean()) if len(velas) else None,
    )


# --- Detección ----------------------------------------------------------------


def _confirmations(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Posiciones de las velas que confirman un rango, y si es alcista.

    Vectorizado sobre pares consecutivos: `inside` es cerrar dentro del rango
    total de la referencia, y las dos tomas son sacarle el mínimo o el máximo.
    La vela que saca los dos extremos es ambigua y se cae aquí; la que saca uno
    y cierra fuera no llega a `inside`.
    """
    previous_high, previous_low = high[:-1], low[:-1]
    inside = (previous_low < close[1:]) & (close[1:] < previous_high)
    took_low = low[1:] < previous_low
    took_high = high[1:] > previous_high
    bullish = inside & took_low & ~took_high
    bearish = inside & took_high & ~took_low
    confirm = np.flatnonzero(bullish | bearish) + 1
    return confirm, bullish[confirm - 1]


def _size_in_atr(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, reference: np.ndarray, size: np.ndarray
) -> np.ndarray:
    """El tamaño del rango medido en ATR(14) de la VELA DE REFERENCIA.

    En la referencia y no en la de confirmación: lo que se quiere saber es si el
    rango era grande para la volatilidad que había ANTES de la manipulación. El
    ATR de la vela `i` ya lleva dentro la mecha que se acaba de sacar, así que
    con ella un rango grande se mediría contra su propio empujón.
    """
    reference_atr = atr(high, low, close, ATR_PERIOD)[reference]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(reference_atr > 0, size / reference_atr, np.nan)


def _resolve(
    high: np.ndarray,
    low: np.ndarray,
    confirm: np.ndarray,
    bullish: np.ndarray,
    target: np.ndarray,
    invalidation: np.ndarray,
    max_candles_alive: int | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ciclo de vida de cada rango sobre las velas POSTERIORES a la que lo confirmó.

    Cuando la misma vela toca el objetivo y la invalidación se resuelve como
    FALLIDO —criterio conservador: dentro de una vela no se sabe qué pasó
    primero— y se marca `ambiguous_resolution`.
    """
    total = len(high)
    status = np.full(len(confirm), ACTIVE, dtype=object)
    resolved = np.full(len(confirm), -1, dtype=np.int64)
    ambiguous = np.zeros(len(confirm), dtype=bool)

    for position, start in enumerate(confirm):
        first = int(start) + 1
        last = total if max_candles_alive is None else min(total, first + max_candles_alive)
        if last <= first:
            continue
        if bullish[position]:
            hit = high[first:last] >= target[position]
            broke = low[first:last] < invalidation[position]
        else:
            hit = low[first:last] <= target[position]
            broke = high[first:last] > invalidation[position]

        first_hit = int(np.argmax(hit)) if hit.any() else -1
        first_broke = int(np.argmax(broke)) if broke.any() else -1

        if first_broke >= 0 and (first_hit < 0 or first_broke <= first_hit):
            status[position] = FAILED
            resolved[position] = first + first_broke
            ambiguous[position] = first_broke == first_hit
        elif first_hit >= 0:
            status[position] = COMPLETED
            resolved[position] = first + first_hit
        elif max_candles_alive is not None and last == first + max_candles_alive:
            # Sobrevivió a todas las velas que se le daban: expira en la última.
            status[position] = EXPIRED
            resolved[position] = last - 1

    return status, resolved, ambiguous


def _as_utc(moment: pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(moment)
    return stamp.tz_localize("UTC") if stamp.tz is None else stamp.tz_convert("UTC")
