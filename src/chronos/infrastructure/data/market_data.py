"""Adaptadores del puerto `MarketData`.

Todos devuelven barras en el formato canónico de `domain.bars` y recortan aquí
el rango pedido: la estrategia nunca ve una barra posterior a `until`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from chronos.application.backtest.config import DataConfig
from chronos.domain.bars import normalize_bars, resample_bars, slice_bars
from chronos.domain.enums import Timeframe
from chronos.domain.errors import DomainError
from chronos.infrastructure.data.synthetic import generate_ohlcv


class FrameMarketData:
    """Adaptador simulado: histórico en memoria. La base de los demás.

    Guarda las barras en su timeframe nativo y resamplea bajo demanda.
    """

    def __init__(self, frame: pd.DataFrame, symbol: str, base_timeframe: Timeframe) -> None:
        self._frame = frame
        self._symbol = symbol
        self._base = base_timeframe

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    def bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> pd.DataFrame:
        if symbol.upper() != self._symbol.upper():
            raise DomainError(f"Esta fuente sirve {self._symbol}, no {symbol}")
        if timeframe.minutes < self._base.minutes:
            raise DomainError(
                f"No se puede pasar de {self._base.value} a {timeframe.value}: "
                "el timeframe pedido es más fino que el de los datos"
            )

        # El recorte va antes del resampleo: así una vela agregada nunca se
        # forma con barras posteriores a `until`.
        frame = slice_bars(self._frame, since, until)
        if timeframe is not self._base:
            frame = _drop_unclosed_tail(resample_bars(frame, timeframe), timeframe, self._base, frame)
        return frame


class ParquetMarketData(FrameMarketData):
    """Histórico en parquet: el formato de trabajo del proyecto."""

    def __init__(self, path: str | Path, symbol: str, base_timeframe: Timeframe) -> None:
        file_path = Path(path)
        if not file_path.is_file():
            raise DomainError(
                f"No se encontró el histórico en {file_path}. "
                "Genera datos con `chronos data synth` o importa los tuyos con `chronos data import`."
            )
        super().__init__(normalize_bars(pd.read_parquet(file_path)), symbol, base_timeframe)


class CsvMarketData(FrameMarketData):
    """Histórico en CSV (exportaciones de cTrader, Dukascopy, MT5...)."""

    def __init__(
        self,
        path: str | Path,
        symbol: str,
        base_timeframe: Timeframe,
        timezone: str = "UTC",
    ) -> None:
        file_path = Path(path)
        if not file_path.is_file():
            raise DomainError(f"No se encontró el CSV en {file_path}")
        super().__init__(
            normalize_bars(pd.read_csv(file_path), timezone=timezone), symbol, base_timeframe
        )


class SyntheticMarketData(FrameMarketData):
    """Datos generados. Solo para pruebas de humo del motor."""

    def __init__(
        self,
        symbol: str = "XAUUSD",
        base_timeframe: Timeframe = Timeframe.M1,
        periods: int = 50_000,
        seed: int = 42,
    ) -> None:
        frame = generate_ohlcv(
            periods=periods, freq=base_timeframe.pandas_freq, seed=seed, spread_points=20
        )
        super().__init__(frame, symbol, base_timeframe)


def _drop_unclosed_tail(
    bars: pd.DataFrame, timeframe: Timeframe, base: Timeframe, source: pd.DataFrame
) -> pd.DataFrame:
    """Descarta la última vela agregada si todavía no ha cerrado.

    La vela etiquetada en `L` cubre [L, L + timeframe) y solo está cerrada
    cuando los datos llegan hasta el final de ese intervalo. Entregarla a medio
    formar sería look-ahead al revés: la estrategia decidiría sobre una vela que
    en ese instante aún no existe. Solo la última puede estar incompleta.
    """
    if bars.empty or source.empty:
        return bars
    closes_at = bars.index[-1] + pd.Timedelta(minutes=timeframe.minutes)
    covered_until = source.index[-1] + pd.Timedelta(minutes=base.minutes)
    return bars.iloc[:-1] if closes_at > covered_until else bars


def build_market_data(config: DataConfig, symbol: str) -> FrameMarketData:
    """Fábrica dirigida por configuración."""
    match config.source:
        case "parquet":
            return ParquetMarketData(config.path, symbol, config.timeframe)
        case "csv":
            return CsvMarketData(config.path, symbol, config.timeframe, config.timezone)
        case "synthetic":
            return SyntheticMarketData(symbol, config.timeframe)
        case _:
            raise DomainError(f"Fuente de datos desconocida: {config.source}")
