"""Operación cerrada: el registro inmutable que alimenta todas las métricas.

Convención de signos: `commission` y `swap` se almacenan con su signo real
(negativo = coste), de modo que `net_pnl = gross_pnl + commission + swap`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from chronos.domain.enums import ExitReason, Side


@dataclass(frozen=True, slots=True)
class Trade:
    id: int
    symbol: str
    side: Side
    volume: float
    entry_price: float
    entry_time: datetime
    entry_index: int
    exit_price: float
    exit_time: datetime
    exit_index: int
    reason: ExitReason
    gross_pnl: float
    commission: float
    swap: float
    net_pnl: float
    initial_stop_loss: float | None = None
    take_profit: float | None = None
    risk_amount: float | None = None
    mae: float = 0.0
    mfe: float = 0.0
    tag: str = ""
    comment: str = ""

    @property
    def is_winner(self) -> bool:
        return self.net_pnl > 0

    @property
    def duration(self) -> timedelta:
        return self.exit_time - self.entry_time

    @property
    def bars_held(self) -> int:
        return self.exit_index - self.entry_index

    @property
    def r_multiple(self) -> float | None:
        """Resultado en múltiplos del riesgo inicial. `None` si no hubo stop."""
        if self.risk_amount is None or self.risk_amount <= 0:
            return None
        return self.net_pnl / self.risk_amount

    @property
    def price_move(self) -> float:
        """Recorrido de precio a favor (positivo) o en contra (negativo)."""
        return self.side.sign * (self.exit_price - self.entry_price)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        data["reason"] = self.reason.value
        data["duration_minutes"] = self.duration.total_seconds() / 60.0
        data["bars_held"] = self.bars_held
        data["r_multiple"] = self.r_multiple
        return data
