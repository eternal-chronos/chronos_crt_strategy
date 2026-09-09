"""Vocabulario del dominio de trading."""

from __future__ import annotations

from enum import StrEnum


class Side(StrEnum):
    """Dirección de una posición u orden."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 para largos, -1 para cortos. Simplifica el cálculo de P&L."""
        return 1 if self is Side.BUY else -1

    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class ExitReason(StrEnum):
    """Por qué se cerró una posición. Clave para diagnosticar la estrategia."""

    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    SIGNAL = "SIGNAL"
    OPPOSITE_SIGNAL = "OPPOSITE_SIGNAL"
    SESSION_CLOSE = "SESSION_CLOSE"
    TIME_STOP = "TIME_STOP"
    RISK_LIMIT = "RISK_LIMIT"
    STOP_OUT = "STOP_OUT"
    END_OF_DATA = "END_OF_DATA"


class Timeframe(StrEnum):
    """Timeframes soportados, con su duración en minutos."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def minutes(self) -> int:
        return _TIMEFRAME_MINUTES[self]

    @property
    def pandas_freq(self) -> str:
        """Alias de frecuencia para `DataFrame.resample`."""
        return _TIMEFRAME_FREQ[self]

    @classmethod
    def parse(cls, raw: str) -> Timeframe:
        return cls(raw.strip().upper())


_TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}

_TIMEFRAME_FREQ: dict[Timeframe, str] = {
    Timeframe.M1: "1min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.M30: "30min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1D",
}
