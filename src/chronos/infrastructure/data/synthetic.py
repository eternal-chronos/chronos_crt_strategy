"""Generador de datos sintéticos de XAUUSD.

Existe por una razón concreta: permite ejercitar el motor, las métricas y los
informes de punta a punta antes de tener histórico real. NO sirve para evaluar
una estrategia — un GBM no tiene la microestructura ni la autocorrelación del
oro. Úsalo solo para pruebas de humo y tests.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Volatilidad anualizada típica del oro (~15%) traducida a la barra de un minuto.
DEFAULT_ANNUAL_VOL = 0.15
MINUTES_PER_YEAR = 365.25 * 24 * 60


def generate_ohlcv(
    *,
    start: str | pd.Timestamp = "2024-01-01",
    periods: int = 20_000,
    freq: str = "1min",
    start_price: float = 2_000.0,
    annual_vol: float = DEFAULT_ANNUAL_VOL,
    drift_annual: float = 0.0,
    seed: int = 42,
    tick_size: float = 0.01,
    spread_points: float | None = None,
) -> pd.DataFrame:
    """Serie OHLCV sintética en formato canónico (índice UTC).

    El precio sigue un movimiento browniano geométrico; el rango de cada barra se
    construye alrededor de open/close para respetar la invariante OHLC.
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")

    minutes = pd.Timedelta(freq).total_seconds() / 60.0
    dt = minutes / MINUTES_PER_YEAR
    sigma = annual_vol * np.sqrt(dt)
    mu = (drift_annual - 0.5 * annual_vol**2) * dt

    returns = rng.normal(mu, sigma, size=periods)
    close = start_price * np.exp(np.cumsum(returns))
    open_ = np.empty(periods)
    open_[0] = start_price
    open_[1:] = close[:-1]

    # Mecha proporcional al recorrido del cuerpo, con un mínimo de un tick.
    body = np.abs(close - open_)
    wick = np.maximum(body * rng.uniform(0.2, 1.5, size=periods), tick_size)
    high = np.maximum(open_, close) + wick * rng.uniform(0, 1, size=periods)
    low = np.minimum(open_, close) - wick * rng.uniform(0, 1, size=periods)

    frame = pd.DataFrame(
        {
            "open": np.round(open_ / tick_size) * tick_size,
            "high": np.round(high / tick_size) * tick_size,
            "low": np.round(low / tick_size) * tick_size,
            "close": np.round(close / tick_size) * tick_size,
            "volume": rng.integers(50, 500, size=periods).astype(float),
        },
        index=index,
    )
    frame.index.name = "timestamp"

    # El redondeo puede sacar open/close del rango: se reajusta.
    frame["high"] = frame[["open", "high", "close"]].max(axis=1)
    frame["low"] = frame[["open", "low", "close"]].min(axis=1)

    if spread_points is not None:
        base = spread_points * tick_size
        noise = rng.gamma(shape=2.0, scale=0.25, size=periods)
        frame["spread"] = np.round((base * (0.6 + noise)) / tick_size) * tick_size

    return frame
