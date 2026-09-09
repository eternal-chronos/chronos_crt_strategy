"""Contrato canónico de barras. Es el tipo de valor del dominio: un DataFrame.

Formato canónico:
    índice  : DatetimeIndex UTC, monótono, sin duplicados
    columnas: open, high, low, close, volume  (+ spread opcional, en precio)
    la barra en `t` está cerrada en `t`

Aquí no se envuelve nada en entidades: las funciones son puras sobre el
DataFrame y devuelven DataFrames. Sin red, sin ficheros, sin reloj.
"""

from __future__ import annotations

from collections.abc import Hashable
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from chronos.domain.enums import Timeframe
from chronos.domain.errors import InvalidPrice

REQUIRED_COLUMNS = ("open", "high", "low", "close")
OPTIONAL_COLUMNS = ("volume", "spread")
#: Columnas que una estrategia puede leer barra a barra.
PRICE_COLUMNS = ("open", "high", "low", "close", "volume")

_COLUMN_ALIASES = {
    "o": "open", "h": "high", "l": "low", "c": "close",
    "v": "volume", "vol": "volume", "tickvol": "volume", "tick_volume": "volume",
    "bid": "close", "price": "close",
    "date": "timestamp", "datetime": "timestamp", "time": "timestamp",
    "gmt time": "timestamp", "local time": "timestamp", "date_time": "timestamp",
    "<date>": "timestamp", "<open>": "open", "<high>": "high",
    "<low>": "low", "<close>": "close", "<tickvol>": "volume", "<spread>": "spread",
}


def normalize_bars(frame: pd.DataFrame, timezone: str = "UTC") -> pd.DataFrame:
    """Lleva un DataFrame arbitrario al formato canónico.

    Acepta la marca de tiempo como índice o como columna (`timestamp`, `date`,
    `time`, `Gmt time`...). Si es naíf, se interpreta en `timezone` y se
    convierte a UTC.
    """
    df = frame.copy()
    df.columns = [_COLUMN_ALIASES.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns]

    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=False, format="mixed")

    index = pd.DatetimeIndex(df.index)
    index = index.tz_localize(timezone) if index.tz is None else index.tz_convert("UTC")
    df.index = index.tz_convert("UTC") if index.tz is not None else index
    df.index.name = "timestamp"

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise InvalidPrice(f"Faltan columnas obligatorias: {', '.join(missing)}")

    keep = [*REQUIRED_COLUMNS, *(c for c in OPTIONAL_COLUMNS if c in df.columns)]
    df = df[keep].astype(float)
    if "volume" not in df.columns:
        df["volume"] = 0.0

    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=list(REQUIRED_COLUMNS))


def validate_bars(frame: pd.DataFrame) -> None:
    """Comprueba las invariantes del formato canónico. Lanza `InvalidPrice`.

    Se llama al entrar a `application/`, no dentro de cada función.
    """
    if frame.empty:
        raise InvalidPrice("El conjunto de datos está vacío")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise InvalidPrice("El índice debe ser un DatetimeIndex")
    if frame.index.tz is None:
        raise InvalidPrice("El índice debe ser tz-aware (UTC)")
    if not frame.index.is_monotonic_increasing:
        raise InvalidPrice("El índice no está ordenado cronológicamente")
    if frame.index.has_duplicates:
        raise InvalidPrice("Hay marcas de tiempo duplicadas")

    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise InvalidPrice(f"Faltan columnas obligatorias: {', '.join(missing)}")

    bad_range = frame["high"] < frame["low"]
    if bool(bad_range.any()):
        first = frame.index[bad_range][0]
        raise InvalidPrice(f"Barras con high < low, la primera en {first}")

    outside = (
        (frame["open"] > frame["high"]) | (frame["open"] < frame["low"])
        | (frame["close"] > frame["high"]) | (frame["close"] < frame["low"])
    )
    if bool(outside.any()):
        first = frame.index[outside][0]
        raise InvalidPrice(f"Barras con open/close fuera del rango, la primera en {first}")

    if bool((frame[list(REQUIRED_COLUMNS)] <= 0).any().any()):
        raise InvalidPrice("Hay precios menores o iguales a cero")


def resample_bars(frame: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """Reagrupa a un timeframe superior.

    Las barras se etiquetan con el inicio de su intervalo (convención de las
    plataformas de trading): la vela M15 de las 10:00 cubre [10:00, 10:15).
    """
    aggregation: dict[Hashable, Any] = {
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }
    if "spread" in frame.columns:
        aggregation["spread"] = "mean"

    resampled = frame.resample(timeframe.pandas_freq, label="left", closed="left").agg(aggregation)
    return resampled.dropna(subset=["open", "high", "low", "close"])


def slice_bars(
    frame: pd.DataFrame, since: datetime | None = None, until: datetime | None = None
) -> pd.DataFrame:
    """Recorta por fechas, ambos extremos incluidos. Límites naíf se asumen UTC.

    El recorte por `until` es lo que impide el look-ahead: lo hace el adaptador,
    no la estrategia.
    """
    result = frame
    if since is not None:
        result = result[result.index >= _as_utc(since)]
    if until is not None:
        result = result[result.index <= _as_utc(until)]
    return result


def columns_as_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """Vistas numpy de las columnas de precio, para el bucle del motor."""
    arrays = {name: frame[name].to_numpy(dtype=float) for name in PRICE_COLUMNS if name in frame}
    if "spread" in frame.columns:
        arrays["spread"] = frame["spread"].to_numpy(dtype=float)
    return arrays


def _as_utc(moment: datetime) -> pd.Timestamp:
    stamp = pd.Timestamp(moment)
    return stamp.tz_localize("UTC") if stamp.tz is None else stamp.tz_convert("UTC")
