"""Posición abierta. Entidad con identidad y ciclo de vida propio."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from chronos.domain.enums import ExitReason, Side
from chronos.domain.errors import InvalidOrder, PositionAlreadyClosed
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.trade import Trade


@dataclass(slots=True)
class Position:
    """Posición viva en el mercado.

    Los stops se mueven mediante `move_stop` / `move_take_profit`, que protegen
    la invariante de lado; nunca asignando los atributos directamente.
    """

    id: int
    symbol: str
    side: Side
    volume: float
    entry_price: float
    entry_time: datetime
    entry_index: int
    stop_loss: float | None = None
    take_profit: float | None = None
    initial_stop_loss: float | None = field(default=None, init=False)
    commission_paid: float = 0.0
    swap_accrued: float = 0.0
    margin: float = 0.0
    best_price: float = field(init=False)
    worst_price: float = field(init=False)
    tag: str = ""
    comment: str = ""
    is_open: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if self.volume <= 0:
            raise InvalidOrder("El volumen de una posición debe ser positivo")
        self.initial_stop_loss = self.stop_loss
        self.best_price = self.entry_price
        self.worst_price = self.entry_price
        if self.stop_loss is not None:
            self._assert_stop_side(self.stop_loss)
        if self.take_profit is not None:
            self._assert_target_side(self.take_profit)

    # --- Invariantes --------------------------------------------------------

    def _assert_stop_side(self, price: float) -> None:
        if self.side is Side.BUY and price >= self.entry_price:
            raise InvalidOrder("Stop loss de un largo por encima de la entrada")
        if self.side is Side.SELL and price <= self.entry_price:
            raise InvalidOrder("Stop loss de un corto por debajo de la entrada")

    def _assert_target_side(self, price: float) -> None:
        if self.side is Side.BUY and price <= self.entry_price:
            raise InvalidOrder("Take profit de un largo por debajo de la entrada")
        if self.side is Side.SELL and price >= self.entry_price:
            raise InvalidOrder("Take profit de un corto por encima de la entrada")

    def _assert_open(self) -> None:
        if not self.is_open:
            raise PositionAlreadyClosed(f"La posición {self.id} ya está cerrada")

    # --- Mutaciones controladas --------------------------------------------

    def move_stop(self, price: float, *, allow_adverse: bool = False) -> None:
        """Reubica el stop loss. Por defecto solo se permite moverlo a favor."""
        self._assert_open()
        if not allow_adverse and self.stop_loss is not None:
            improves = (
                price > self.stop_loss if self.side is Side.BUY else price < self.stop_loss
            )
            if not improves:
                raise InvalidOrder("El stop solo puede moverse a favor de la posición")
        self.stop_loss = price

    def move_take_profit(self, price: float) -> None:
        self._assert_open()
        self._assert_target_side(price)
        self.take_profit = price

    def accrue_swap(self, amount: float) -> None:
        self._assert_open()
        self.swap_accrued += amount

    def update_excursion(self, high: float, low: float) -> None:
        """Actualiza MAE/MFE con el recorrido de la barra."""
        if self.side is Side.BUY:
            self.best_price = max(self.best_price, high)
            self.worst_price = min(self.worst_price, low)
        else:
            self.best_price = min(self.best_price, low)
            self.worst_price = max(self.worst_price, high)

    # --- Consultas ----------------------------------------------------------

    def unrealized_pnl(self, spec: InstrumentSpec, price: float) -> float:
        """P&L flotante BRUTO al precio dado.

        No incluye comisión ni swap: el bróker simulado ya los ha descontado del
        balance en el momento en que se devengaron, así que sumarlos aquí los
        contaría dos veces en el equity.
        """
        return spec.gross_pnl(self.side, self.entry_price, price, self.volume)

    def unrealized_pnl_net(self, spec: InstrumentSpec, price: float) -> float:
        """P&L flotante después de comisiones pagadas y swap acumulado."""
        return self.unrealized_pnl(spec, price) + self.commission_paid + self.swap_accrued

    def risk_amount(self, spec: InstrumentSpec) -> float | None:
        """Pérdida esperada si salta el stop inicial. `None` si no había stop."""
        if self.initial_stop_loss is None:
            return None
        distance = abs(self.entry_price - self.initial_stop_loss)
        return distance * spec.contract_size * self.volume

    def mae(self, spec: InstrumentSpec) -> float:
        """Máxima excursión adversa, en divisa de cuenta (valor negativo)."""
        return spec.gross_pnl(self.side, self.entry_price, self.worst_price, self.volume)

    def mfe(self, spec: InstrumentSpec) -> float:
        """Máxima excursión favorable, en divisa de cuenta (valor positivo)."""
        return spec.gross_pnl(self.side, self.entry_price, self.best_price, self.volume)

    # --- Cierre -------------------------------------------------------------

    def close(
        self,
        *,
        spec: InstrumentSpec,
        exit_price: float,
        exit_time: datetime,
        exit_index: int,
        reason: ExitReason,
        exit_commission: float = 0.0,
    ) -> Trade:
        """Cierra la posición y produce el registro inmutable de la operación."""
        self._assert_open()
        gross = spec.gross_pnl(self.side, self.entry_price, exit_price, self.volume)
        commission = self.commission_paid + exit_commission
        net = gross + commission + self.swap_accrued
        risk = self.risk_amount(spec)
        self.is_open = False
        return Trade(
            id=self.id,
            symbol=self.symbol,
            side=self.side,
            volume=self.volume,
            entry_price=self.entry_price,
            entry_time=self.entry_time,
            entry_index=self.entry_index,
            exit_price=exit_price,
            exit_time=exit_time,
            exit_index=exit_index,
            reason=reason,
            gross_pnl=gross,
            commission=commission,
            swap=self.swap_accrued,
            net_pnl=net,
            initial_stop_loss=self.initial_stop_loss,
            take_profit=self.take_profit,
            risk_amount=risk,
            mae=self.mae(spec),
            mfe=self.mfe(spec),
            tag=self.tag,
            comment=self.comment,
        )
