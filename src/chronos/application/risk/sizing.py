"""Políticas de dimensionamiento de posición."""

from __future__ import annotations

from abc import ABC, abstractmethod

from chronos.application.backtest.config import RiskConfig
from chronos.domain.errors import DomainError
from chronos.domain.instrument import InstrumentSpec


class PositionSizer(ABC):
    """Decide cuántos lotes operar. Devolver 0.0 significa 'no operar'."""

    @abstractmethod
    def size(
        self,
        *,
        spec: InstrumentSpec,
        equity: float,
        entry_price: float,
        stop_loss: float | None,
    ) -> float: ...

    @staticmethod
    def _cap(spec: InstrumentSpec, lots: float, max_lot: float) -> float:
        return spec.normalize_volume(min(lots, max_lot))


class FixedLotSizer(PositionSizer):
    """Siempre el mismo lotaje. Útil para comparar señales sin ruido de sizing."""

    def __init__(self, lots: float, max_lot: float) -> None:
        if lots <= 0:
            raise DomainError("fixed_lot debe ser positivo")
        self._lots = lots
        self._max_lot = max_lot

    def size(
        self,
        *,
        spec: InstrumentSpec,
        equity: float,
        entry_price: float,
        stop_loss: float | None,
    ) -> float:
        return self._cap(spec, self._lots, self._max_lot)


class FixedFractionalSizer(PositionSizer):
    """Arriesga un % fijo del equity por operación. Requiere stop loss."""

    def __init__(self, risk_per_trade: float, max_lot: float) -> None:
        if not 0 < risk_per_trade < 1:
            raise DomainError("risk_per_trade debe estar en (0, 1)")
        self._risk = risk_per_trade
        self._max_lot = max_lot

    def size(
        self,
        *,
        spec: InstrumentSpec,
        equity: float,
        entry_price: float,
        stop_loss: float | None,
    ) -> float:
        if stop_loss is None or equity <= 0:
            return 0.0
        distance = abs(entry_price - stop_loss)
        if distance <= 0:
            return 0.0
        lots = spec.lots_for_risk(equity * self._risk, distance)
        return self._cap(spec, lots, self._max_lot)


class FixedCashRiskSizer(PositionSizer):
    """Arriesga una cantidad fija en divisa de cuenta por operación."""

    def __init__(self, cash_risk: float, max_lot: float) -> None:
        if cash_risk <= 0:
            raise DomainError("fixed_cash_risk debe ser positivo")
        self._cash = cash_risk
        self._max_lot = max_lot

    def size(
        self,
        *,
        spec: InstrumentSpec,
        equity: float,
        entry_price: float,
        stop_loss: float | None,
    ) -> float:
        if stop_loss is None:
            return 0.0
        distance = abs(entry_price - stop_loss)
        if distance <= 0:
            return 0.0
        return self._cap(spec, spec.lots_for_risk(self._cash, distance), self._max_lot)


def build_sizer(config: RiskConfig) -> PositionSizer:
    """Fábrica dirigida por configuración."""
    match config.sizing:
        case "fixed_lot":
            return FixedLotSizer(config.fixed_lot, config.max_lot)
        case "fixed_fractional":
            return FixedFractionalSizer(config.risk_per_trade, config.max_lot)
        case "fixed_cash_risk":
            return FixedCashRiskSizer(config.fixed_cash_risk, config.max_lot)
        case _:
            raise DomainError(f"Política de sizing desconocida: {config.sizing}")
