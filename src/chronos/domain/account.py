"""Cuenta de trading: balance, equity y margen."""

from __future__ import annotations

from dataclasses import dataclass, field

from chronos.domain.errors import DomainError, InsufficientMargin


@dataclass(slots=True)
class Account:
    """Estado monetario de la cuenta.

    `balance` solo cambia al cerrar operaciones y al aplicar swap/comisiones.
    `equity` = balance + P&L flotante de las posiciones abiertas.
    """

    initial_balance: float
    currency: str = "USD"
    leverage: float = 200.0
    stop_out_level: float = 0.5
    margin_call_level: float = 1.0
    balance: float = field(init=False)
    equity: float = field(init=False)
    used_margin: float = field(default=0.0, init=False)
    peak_equity: float = field(init=False)

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise DomainError("El balance inicial debe ser positivo")
        if not 0 < self.stop_out_level <= self.margin_call_level:
            raise DomainError("Niveles de margin call / stop out incoherentes")
        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.peak_equity = self.initial_balance

    # --- Mutaciones ---------------------------------------------------------

    def apply_realized(self, amount: float) -> None:
        """Aplica un resultado realizado (P&L de cierre, swap, comisión)."""
        self.balance += amount

    def mark_to_market(self, floating_pnl: float, used_margin: float) -> None:
        """Recalcula el equity con el P&L flotante actual."""
        self.equity = self.balance + floating_pnl
        self.used_margin = used_margin
        self.peak_equity = max(self.peak_equity, self.equity)

    # --- Consultas ----------------------------------------------------------

    @property
    def free_margin(self) -> float:
        return self.equity - self.used_margin

    @property
    def margin_level(self) -> float | None:
        """Equity / margen usado. `None` si no hay posiciones abiertas."""
        if self.used_margin <= 0:
            return None
        return self.equity / self.used_margin

    @property
    def drawdown(self) -> float:
        """Caída absoluta desde el máximo de equity."""
        return self.peak_equity - self.equity

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return self.drawdown / self.peak_equity

    def is_stopped_out(self) -> bool:
        level = self.margin_level
        return level is not None and level < self.stop_out_level

    def is_margin_called(self) -> bool:
        level = self.margin_level
        return level is not None and level < self.margin_call_level

    def require_margin(self, amount: float) -> None:
        """Falla si el margen libre no cubre `amount`."""
        if amount > self.free_margin:
            raise InsufficientMargin(
                f"Margen requerido {amount:.2f} > margen libre {self.free_margin:.2f}"
            )
