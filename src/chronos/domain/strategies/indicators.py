"""Indicadores técnicos vectorizados.

Todos devuelven arrays nuevos y escribibles, alineados con la entrada y con
`NaN` en el periodo de calentamiento (pandas puede devolver vistas de solo
lectura, así que se copia de forma explícita). Ninguno mira hacia el futuro: el valor del índice `i` solo usa
datos de `[0, i]`.
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd


def sma(values: np.ndarray, period: int) -> np.ndarray:
    """Media móvil simple."""
    _check_period(period)
    return pd.Series(values).rolling(period).mean().to_numpy(dtype=float, copy=True)


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Media móvil exponencial (suavizado estándar, alpha = 2/(n+1))."""
    _check_period(period)
    series = pd.Series(values).ewm(span=period, adjust=False).mean()
    result = series.to_numpy(dtype=float, copy=True)
    result[: period - 1] = np.nan  # sin datos suficientes, sin valor
    return result


def rma(values: np.ndarray, period: int) -> np.ndarray:
    """Media suavizada de Wilder (alpha = 1/n). Base de ATR, RSI y ADX."""
    _check_period(period)
    return pd.Series(values).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy(dtype=float, copy=True)


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Rango verdadero: incluye el hueco respecto al cierre anterior."""
    previous_close = np.empty_like(close)
    previous_close[0] = close[0]
    previous_close[1:] = close[:-1]
    return cast(
        np.ndarray,
        np.maximum.reduce([high - low, np.abs(high - previous_close), np.abs(low - previous_close)]),
    )


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range de Wilder."""
    _check_period(period)
    result = rma(true_range(high, low, close), period)
    result[: period - 1] = np.nan
    return result


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Índice de fuerza relativa de Wilder, en [0, 100]."""
    _check_period(period)
    delta = np.diff(close, prepend=close[0])
    gains = rma(np.where(delta > 0, delta, 0.0), period)
    losses = rma(np.where(delta < 0, -delta, 0.0), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(losses > 0, gains / losses, np.inf)
    result = 100.0 - 100.0 / (1.0 + rs)
    result[: period] = np.nan
    return result


def rolling_max(values: np.ndarray, period: int) -> np.ndarray:
    _check_period(period)
    return pd.Series(values).rolling(period).max().to_numpy(dtype=float, copy=True)


def rolling_min(values: np.ndarray, period: int) -> np.ndarray:
    _check_period(period)
    return pd.Series(values).rolling(period).min().to_numpy(dtype=float, copy=True)


def rolling_std(values: np.ndarray, period: int) -> np.ndarray:
    _check_period(period)
    return pd.Series(values).rolling(period).std(ddof=0).to_numpy(dtype=float, copy=True)


def bollinger(
    close: np.ndarray, period: int = 20, deviations: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Devuelve (banda superior, media, banda inferior)."""
    middle = sma(close, period)
    spread = rolling_std(close, period) * deviations
    return middle + spread, middle, middle - spread


def _check_period(period: int) -> None:
    if period < 1:
        raise ValueError("El periodo debe ser >= 1")
