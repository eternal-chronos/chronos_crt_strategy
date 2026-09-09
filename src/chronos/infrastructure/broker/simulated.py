"""Adaptador simulado del puerto `Broker`: traduce intenciones en fills con costes.

Convenio contable (importante para no contar dos veces):
  - Al ABRIR: el balance absorbe la comisión de apertura.
  - Cada ROLLOVER: el balance absorbe el swap.
  - Al CERRAR: el balance absorbe `gross_pnl + comisión de cierre`.
  - El `net_pnl` del `Trade` agrega todo y solo se usa para métricas.
  - El equity se marca a mercado con el P&L BRUTO flotante, porque los costes
    ya están dentro del balance.
"""

from __future__ import annotations

from datetime import datetime

from chronos.application.backtest.config import BacktestConfig, ExecutionConfig
from chronos.domain.account import Account
from chronos.domain.enums import ExitReason, Side
from chronos.domain.errors import InsufficientMargin, InvalidOrder
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.position import Position
from chronos.domain.quote import Quote
from chronos.domain.trade import Trade


class SimulatedBroker:
    """Gestiona posiciones, ejecución, costes y margen durante el backtest."""

    def __init__(
        self,
        spec: InstrumentSpec,
        account: Account,
        execution: ExecutionConfig,
    ) -> None:
        self._spec = spec
        self._account = account
        self._exec = execution
        self._positions: list[Position] = []
        self._trades: list[Trade] = []
        self._next_id = 1

    # --- Estado -------------------------------------------------------------

    @property
    def positions(self) -> list[Position]:
        return self._positions

    @property
    def trades(self) -> list[Trade]:
        return self._trades

    @property
    def account(self) -> Account:
        return self._account

    @property
    def used_margin(self) -> float:
        return sum(p.margin for p in self._positions)

    def floating_pnl(self, quote: Quote) -> float:
        """P&L bruto flotante, valorado al precio de cierre de la posición."""
        return sum(
            self._spec.gross_pnl(p.side, p.entry_price, quote.for_exit(p.side), p.volume)
            for p in self._positions
        )

    def has_position(self, side: Side | None = None) -> bool:
        if side is None:
            return bool(self._positions)
        return any(p.side is side for p in self._positions)

    # --- Precios ------------------------------------------------------------

    def quote(self, price: float, spread: float | None = None) -> Quote:
        """Construye bid/ask a partir del precio del dataset."""
        s = self._effective_spread(spread)
        if self._exec.price_basis == "bid":
            return Quote(bid=price, ask=price + s)
        half = s / 2.0
        return Quote(bid=price - half, ask=price + half)

    def _effective_spread(self, spread: float | None) -> float:
        if not self._exec.apply_spread:
            return 0.0
        costs = self._spec.costs
        if costs.spread_mode == "from_data" and spread is not None:
            return spread
        return self._spec.points_to_price(costs.spread_points)

    @property
    def _slippage(self) -> float:
        if not self._exec.apply_slippage:
            return 0.0
        return self._spec.points_to_price(self._spec.costs.slippage_points)

    # --- Apertura y cierre --------------------------------------------------

    def open_position(
        self,
        *,
        side: Side,
        volume: float,
        quote: Quote,
        timestamp: datetime,
        index: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        tag: str = "",
        comment: str = "",
    ) -> Position:
        """Abre a mercado. Lanza `InsufficientMargin` si no hay margen libre."""
        volume = self._spec.normalize_volume(volume)
        if volume <= 0:
            raise InvalidOrder("El volumen normalizado es cero: por debajo del mínimo operable")

        slip = self._slippage
        entry = quote.for_entry(side) + (slip if side is Side.BUY else -slip)
        entry = self._spec.round_price(entry)

        margin = self._spec.margin_required(volume, entry)
        self._account.require_margin(margin)

        commission = (
            -self._spec.commission(volume, sides=1) if self._exec.apply_commission else 0.0
        )

        position = Position(
            id=self._next_id,
            symbol=self._spec.symbol,
            side=side,
            volume=volume,
            entry_price=entry,
            entry_time=timestamp,
            entry_index=index,
            stop_loss=self._spec.round_price(stop_loss) if stop_loss is not None else None,
            take_profit=self._spec.round_price(take_profit) if take_profit is not None else None,
            commission_paid=commission,
            margin=margin,
            tag=tag,
            comment=comment,
        )
        self._next_id += 1
        self._positions.append(position)
        self._account.apply_realized(commission)
        return position

    def close_position(
        self,
        position: Position,
        *,
        price: float,
        timestamp: datetime,
        index: int,
        reason: ExitReason,
    ) -> Trade:
        """Cierra al precio ya ejecutable indicado (bid/ask + slippage ya aplicados)."""
        commission = (
            -self._spec.commission(position.volume, sides=1)
            if self._exec.apply_commission
            else 0.0
        )
        trade = position.close(
            spec=self._spec,
            exit_price=self._spec.round_price(price),
            exit_time=timestamp,
            exit_index=index,
            reason=reason,
            exit_commission=commission,
        )
        self._account.apply_realized(trade.gross_pnl + commission)
        self._positions.remove(position)
        self._trades.append(trade)
        return trade

    def close_at_market(
        self,
        position: Position,
        *,
        quote: Quote,
        timestamp: datetime,
        index: int,
        reason: ExitReason,
    ) -> Trade:
        """Cierre a mercado: precio de salida con horquilla y slippage adverso."""
        slip = self._slippage
        price = quote.for_exit(position.side) - (slip if position.side is Side.BUY else -slip)
        return self.close_position(
            position, price=price, timestamp=timestamp, index=index, reason=reason
        )

    def close_all(
        self,
        *,
        quote: Quote,
        timestamp: datetime,
        index: int,
        reason: ExitReason,
        side: Side | None = None,
    ) -> list[Trade]:
        targets = [p for p in self._positions if side is None or p.side is side]
        return [
            self.close_at_market(p, quote=quote, timestamp=timestamp, index=index, reason=reason)
            for p in targets
        ]

    def modify_stops(
        self,
        position: Position,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> None:
        """Reubica SL/TP de una posición abierta. Lanza `InvalidOrder` si no es válido."""
        if stop_loss is not None:
            position.move_stop(self._spec.round_price(stop_loss), allow_adverse=True)
        if take_profit is not None:
            position.move_take_profit(self._spec.round_price(take_profit))

    # --- Barrido intrabar de stops -----------------------------------------

    def process_protective_orders(
        self,
        *,
        open_price: float,
        high: float,
        low: float,
        spread: float | None,
        timestamp: datetime,
        index: int,
    ) -> list[Trade]:
        """Comprueba SL/TP contra el recorrido de la barra.

        Modela los huecos: si la barra abre más allá del nivel, el fill ocurre en
        la apertura, no en el nivel. Si la barra toca ambos niveles, resuelve
        según `intrabar_priority` ("worst" = siempre el stop, lo conservador).
        """
        s = self._effective_spread(spread)
        slip = self._slippage
        closed: list[Trade] = []

        for position in list(self._positions):
            position.update_excursion(high, low)
            exit_bid, exit_ask = self._exit_prices(open_price, high, low, s)

            hit_sl, sl_price = self._check_stop_loss(position, exit_bid, exit_ask, slip)
            hit_tp, tp_price = self._check_take_profit(position, exit_bid, exit_ask)

            if hit_sl and hit_tp:
                stop_first = self._exec.intrabar_priority in ("worst", "sl_first")
                hit_tp, hit_sl = (not stop_first, stop_first)

            if hit_sl:
                closed.append(
                    self.close_position(
                        position,
                        price=sl_price,
                        timestamp=timestamp,
                        index=index,
                        reason=ExitReason.STOP_LOSS,
                    )
                )
            elif hit_tp:
                closed.append(
                    self.close_position(
                        position,
                        price=tp_price,
                        timestamp=timestamp,
                        index=index,
                        reason=ExitReason.TAKE_PROFIT,
                    )
                )
        return closed

    def _exit_prices(
        self, open_price: float, high: float, low: float, spread: float
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Devuelve (open, high, low) en bid y en ask para la barra."""
        if self._exec.price_basis == "bid":
            bid = (open_price, high, low)
            ask = (open_price + spread, high + spread, low + spread)
        else:
            half = spread / 2.0
            bid = (open_price - half, high - half, low - half)
            ask = (open_price + half, high + half, low + half)
        return bid, ask

    def _check_stop_loss(
        self,
        position: Position,
        bid: tuple[float, float, float],
        ask: tuple[float, float, float],
        slip: float,
    ) -> tuple[bool, float]:
        level = position.stop_loss
        if level is None:
            return False, 0.0
        if position.side is Side.BUY:
            bid_open, _, bid_low = bid
            if bid_low <= level:
                fill = min(bid_open, level) - slip
                return True, fill
        else:
            ask_open, ask_high, _ = ask
            if ask_high >= level:
                fill = max(ask_open, level) + slip
                return True, fill
        return False, 0.0

    def _check_take_profit(
        self,
        position: Position,
        bid: tuple[float, float, float],
        ask: tuple[float, float, float],
    ) -> tuple[bool, float]:
        """El take profit es una orden limitada: se llena al nivel, sin slippage."""
        level = position.take_profit
        if level is None:
            return False, 0.0
        if position.side is Side.BUY:
            bid_open, bid_high, _ = bid
            if bid_high >= level:
                return True, max(bid_open, level)
        else:
            ask_open, _, ask_low = ask
            if ask_low <= level:
                return True, min(ask_open, level)
        return False, 0.0

    # --- Financiación y margen ---------------------------------------------

    def apply_swap(self, weekday: int) -> float:
        """Carga el swap de una noche a todas las posiciones abiertas."""
        if not self._exec.apply_swap or not self._positions:
            return 0.0
        total = 0.0
        for position in self._positions:
            charge = self._spec.swap_charge(position.side, position.volume, weekday)
            position.accrue_swap(charge)
            total += charge
        self._account.apply_realized(total)
        return total

    def mark_to_market(self, quote: Quote) -> None:
        self._account.mark_to_market(self.floating_pnl(quote), self.used_margin)

    def enforce_stop_out(
        self, *, quote: Quote, timestamp: datetime, index: int
    ) -> list[Trade]:
        """Liquida todo si el nivel de margen cae por debajo del stop out."""
        if not self._account.is_stopped_out():
            return []
        return self.close_all(
            quote=quote, timestamp=timestamp, index=index, reason=ExitReason.STOP_OUT
        )

    # --- Ayudas para el motor ----------------------------------------------

    def can_afford(self, volume: float, price: float) -> bool:
        try:
            self._account.require_margin(self._spec.margin_required(volume, price))
        except InsufficientMargin:
            return False
        return True


def build_simulated_broker(spec: InstrumentSpec, config: BacktestConfig) -> SimulatedBroker:
    """Construye bróker y cuenta a partir de la configuración de la corrida."""
    account = Account(
        initial_balance=config.account.initial_balance,
        currency=config.account.currency,
        leverage=config.account.leverage,
        stop_out_level=config.account.stop_out_level,
        margin_call_level=config.account.margin_call_level,
    )
    return SimulatedBroker(spec, account, config.execution)
