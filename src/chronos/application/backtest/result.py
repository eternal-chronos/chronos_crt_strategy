"""Resultado de una corrida de backtest."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from chronos.domain.enums import Timeframe
from chronos.domain.trade import Trade


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Todo lo que produce una corrida: operaciones, curva de equity y contexto.

    Las métricas se calculan aparte (`chronos.application.metrics`) para que el
    motor no dependa de cómo se evalúa el rendimiento.
    """

    symbol: str
    timeframe: Timeframe
    initial_balance: float
    trades: Sequence[Trade]
    equity_curve: pd.DataFrame  # index=timestamp; columnas: balance, equity, exposure
    strategy: dict[str, Any]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    bars_processed: int = 0
    halted_reason: str | None = None
    rejections: dict[str, int] = field(default_factory=dict)

    @property
    def final_balance(self) -> float:
        if self.equity_curve.empty:
            return self.initial_balance
        return float(self.equity_curve["balance"].iloc[-1])

    @property
    def final_equity(self) -> float:
        if self.equity_curve.empty:
            return self.initial_balance
        return float(self.equity_curve["equity"].iloc[-1])

    def trades_frame(self) -> pd.DataFrame:
        """Operaciones cerradas como DataFrame, listo para análisis o CSV."""
        if not self.trades:
            return pd.DataFrame(
                columns=[
                    "id", "symbol", "side", "volume", "entry_time", "entry_price",
                    "exit_time", "exit_price", "reason", "gross_pnl", "commission",
                    "swap", "net_pnl", "r_multiple", "mae", "mfe", "bars_held", "tag",
                ]
            )
        return pd.DataFrame([t.to_dict() for t in self.trades])
