"""Motor de backtest barra a barra.

Orden de eventos dentro de cada barra (importante para no introducir lookahead):
    1. Devengo de swap si la barra cruza el rollover.
    2. Ejecución de las intenciones emitidas en la barra anterior, a la apertura.
    3. Barrido intrabar de stop loss / take profit sobre el recorrido de la barra.
    4. Marcado a mercado al cierre y control de stop out.
    5. Controles de riesgo (pérdida diaria, drawdown máximo).
    6. Llamada a la estrategia con la barra ya cerrada.

La estrategia nunca ve la barra en la que se ejecuta su señal, salvo que se
configure `fill_model = current_close` (modelo optimista, solo para depurar).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.application.backtest.config import BacktestConfig
from chronos.application.backtest.result import BacktestResult
from chronos.application.backtest.session import BarFlags, build_bar_flags
from chronos.application.ports import Broker
from chronos.application.risk.sizing import PositionSizer, build_sizer
from chronos.domain.bars import columns_as_arrays
from chronos.domain.context import BarContext
from chronos.domain.enums import ExitReason
from chronos.domain.errors import DomainError, InvalidOrder
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.quote import Quote
from chronos.domain.signal import EntrySignal, ExitSignal, ModifyStops, StrategyAction
from chronos.domain.strategy import Strategy


class BacktestEngine:
    """Ejecuta una estrategia sobre una serie histórica."""

    def __init__(self, spec: InstrumentSpec, config: BacktestConfig, broker: Broker) -> None:
        self._spec = spec
        self._config = config
        self._broker = broker
        self._sizer: PositionSizer = build_sizer(config.risk)

    def run(self, bars: pd.DataFrame, strategy: Strategy) -> BacktestResult:
        if bars.empty:
            raise DomainError("No hay barras para simular")

        broker = self._broker
        account = broker.account
        timestamps = pd.DatetimeIndex(bars.index)
        context = BarContext(bars, self._spec)
        flags = build_bar_flags(timestamps, self._spec)

        strategy.prepare(bars)

        state = _RunState(
            warmup=max(strategy.warmup_bars, 1),
            day_id=int(flags.day_id[0]),
            day_start_balance=account.balance,
        )
        series = _Series(bars)
        equity_track = _EquityTrack(len(bars))

        for i in range(len(bars)):
            self._process_bar(i, timestamps, series, flags, broker, context, strategy, state)
            equity_track.record(i, account.balance, account.equity, len(broker.positions))
            if state.halted_reason is not None:
                equity_track.truncate(i + 1)
                break

        # Liquidación de lo que quede abierto, al cierre de la última barra.
        last = min(equity_track.length, len(bars)) - 1
        if broker.positions:
            quote = broker.quote(series.close[last], series.spread_at(last))
            broker.close_all(
                quote=quote,
                timestamp=timestamps[last].to_pydatetime(),
                index=last,
                reason=ExitReason.END_OF_DATA,
            )
            broker.mark_to_market(quote)
            equity_track.record(last, account.balance, account.equity, 0)

        strategy.on_finish()

        return BacktestResult(
            symbol=self._spec.symbol,
            timeframe=self._config.data.strategy_timeframe,
            initial_balance=self._config.account.initial_balance,
            trades=tuple(broker.trades),
            equity_curve=equity_track.to_frame(timestamps),
            strategy=strategy.describe(),
            started_at=timestamps[0].to_pydatetime(),
            ended_at=timestamps[last].to_pydatetime(),
            bars_processed=equity_track.length,
            halted_reason=state.halted_reason,
            rejections=dict(state.rejections),
        )

    # --- Bucle --------------------------------------------------------------

    def _process_bar(
        self,
        i: int,
        timestamps: pd.DatetimeIndex,
        series: _Series,
        flags: BarFlags,
        broker: Broker,
        context: BarContext,
        strategy: Strategy,
        state: _RunState,
    ) -> None:
        timestamp = timestamps[i].to_pydatetime()
        spread = series.spread_at(i)
        open_quote = broker.quote(series.open[i], spread)

        # 1. Swap nocturno.
        if flags.is_rollover[i]:
            broker.apply_swap(int(flags.swap_weekday[i]))
            self._roll_day(int(flags.day_id[i]), broker, state)

        # 2. Intenciones de la barra anterior, ejecutadas a la apertura.
        if state.pending:
            self._execute(state.pending, broker, open_quote, timestamp, i, state, strategy)
            state.pending = ()

        # 3. Stops y objetivos dentro de la barra.
        closed = broker.process_protective_orders(
            open_price=series.open[i],
            high=series.high[i],
            low=series.low[i],
            spread=spread,
            timestamp=timestamp,
            index=i,
        )
        for trade in closed:
            strategy.on_trade_closed(trade)

        # 4. Marcado a mercado al cierre y stop out.
        close_quote = broker.quote(series.close[i], spread)
        broker.mark_to_market(close_quote)
        for trade in broker.enforce_stop_out(quote=close_quote, timestamp=timestamp, index=i):
            strategy.on_trade_closed(trade)
            state.rejections["stop_out"] += 1

        # 5. Controles de riesgo.
        self._enforce_risk_limits(broker, close_quote, timestamp, i, state, strategy)
        if state.halted_reason is not None:
            return

        # 6. Decisión de la estrategia sobre la barra ya cerrada.
        if i < state.warmup or not flags.session_open[i]:
            return
        context.update(
            index=i,
            equity=broker.account.equity,
            balance=broker.account.balance,
            positions=broker.positions,
        )
        actions = strategy.on_bar(context)
        if not actions:
            return
        if self._config.execution.fill_model == "current_close":
            self._execute(actions, broker, close_quote, timestamp, i, state, strategy)
            broker.mark_to_market(close_quote)
        else:
            state.pending = tuple(actions)

    # --- Riesgo -------------------------------------------------------------

    def _roll_day(self, day_id: int, broker: Broker, state: _RunState) -> None:
        state.day_id = day_id
        state.day_start_balance = broker.account.balance
        state.day_blocked = False

    def _enforce_risk_limits(
        self,
        broker: Broker,
        quote: Quote,
        timestamp: datetime,
        index: int,
        state: _RunState,
        strategy: Strategy,
    ) -> None:
        risk = self._config.risk
        account = broker.account

        if risk.max_drawdown_stop is not None and account.drawdown_pct >= risk.max_drawdown_stop:
            for trade in broker.close_all(
                quote=quote,
                timestamp=timestamp,
                index=index,
                reason=ExitReason.RISK_LIMIT,
            ):
                strategy.on_trade_closed(trade)
            broker.mark_to_market(quote)
            state.halted_reason = (
                f"Drawdown máximo alcanzado ({account.drawdown_pct:.1%}) en la barra {index}"
            )
            return

        if risk.max_daily_loss is not None and not state.day_blocked:
            daily_pnl = account.equity - state.day_start_balance
            limit = -abs(risk.max_daily_loss) * self._config.account.initial_balance
            if daily_pnl <= limit:
                for trade in broker.close_all(
                    quote=quote,
                    timestamp=timestamp,
                    index=index,
                    reason=ExitReason.RISK_LIMIT,
                ):
                    strategy.on_trade_closed(trade)
                broker.mark_to_market(quote)
                state.day_blocked = True
                state.rejections["daily_loss_limit"] += 1

    # --- Ejecución de intenciones ------------------------------------------

    def _execute(
        self,
        actions: Sequence[StrategyAction],
        broker: Broker,
        quote: Quote,
        timestamp: datetime,
        index: int,
        state: _RunState,
        strategy: Strategy,
    ) -> None:
        for action in actions:
            match action:
                case EntrySignal():
                    self._open(action, broker, quote, timestamp, index, state)
                case ExitSignal():
                    self._close(action, broker, quote, timestamp, index, strategy)
                case ModifyStops():
                    self._modify(action, broker, state)
                case _:  # pragma: no cover - defensivo
                    raise DomainError(f"Acción de estrategia desconocida: {action!r}")

    def _open(
        self,
        signal: EntrySignal,
        broker: Broker,
        quote: Quote,
        timestamp: datetime,
        index: int,
        state: _RunState,
    ) -> None:
        if state.day_blocked:
            state.rejections["day_blocked"] += 1
            return
        if len(broker.positions) >= self._config.risk.max_concurrent_positions:
            state.rejections["max_positions"] += 1
            return

        # Los niveles se resuelven contra el precio ejecutable real, no contra el
        # cierre sobre el que decidió la estrategia.
        reference = quote.for_entry(signal.side)
        try:
            signal.validate_against(reference)
        except InvalidOrder:
            state.rejections["invalid_stops"] += 1
            return
        stop_loss, take_profit = signal.levels(reference)

        volume = self._resolve_volume(signal, broker, reference, stop_loss)
        if volume <= 0:
            state.rejections["zero_volume"] += 1
            return
        if not broker.can_afford(volume, reference):
            state.rejections["insufficient_margin"] += 1
            return

        try:
            broker.open_position(
                side=signal.side,
                volume=volume,
                quote=quote,
                timestamp=timestamp,
                index=index,
                stop_loss=stop_loss,
                take_profit=take_profit,
                tag=signal.tag,
                comment=signal.comment,
            )
        except (InvalidOrder, DomainError):
            state.rejections["rejected_by_broker"] += 1

    def _resolve_volume(
        self,
        signal: EntrySignal,
        broker: Broker,
        reference: float,
        stop_loss: float | None,
    ) -> float:
        if signal.volume is not None:
            return self._spec.normalize_volume(min(signal.volume, self._config.risk.max_lot))
        equity = broker.account.equity
        if signal.risk_fraction is not None and stop_loss is not None:
            distance = abs(reference - stop_loss)
            if distance <= 0:
                return 0.0
            lots = self._spec.lots_for_risk(equity * signal.risk_fraction, distance)
            return self._spec.normalize_volume(min(lots, self._config.risk.max_lot))
        return self._sizer.size(
            spec=self._spec,
            equity=equity,
            entry_price=reference,
            stop_loss=stop_loss,
        )

    def _close(
        self,
        signal: ExitSignal,
        broker: Broker,
        quote: Quote,
        timestamp: datetime,
        index: int,
        strategy: Strategy,
    ) -> None:
        targets = [
            p
            for p in list(broker.positions)
            if signal.position_id is None or p.id == signal.position_id
        ]
        for position in targets:
            trade = broker.close_at_market(
                position,
                quote=quote,
                timestamp=timestamp,
                index=index,
                reason=signal.reason,
            )
            strategy.on_trade_closed(trade)

    def _modify(self, signal: ModifyStops, broker: Broker, state: _RunState) -> None:
        targets = [
            p
            for p in broker.positions
            if signal.position_id is None or p.id == signal.position_id
        ]
        for position in targets:
            try:
                broker.modify_stops(
                    position,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
            except InvalidOrder:
                state.rejections["invalid_modify"] += 1


class _RunState:
    """Estado mutable del bucle, aislado del motor para mantenerlo legible."""

    __slots__ = (
        "day_blocked",
        "day_id",
        "day_start_balance",
        "halted_reason",
        "pending",
        "rejections",
        "warmup",
    )

    def __init__(self, warmup: int, day_id: int, day_start_balance: float) -> None:
        self.warmup = warmup
        self.pending: tuple[StrategyAction, ...] = ()
        self.day_id = day_id
        self.day_start_balance = day_start_balance
        self.day_blocked = False
        self.halted_reason: str | None = None
        self.rejections: Counter[str] = Counter()


class _Series:
    """Vistas numpy de las columnas, para evitar conversiones dentro del bucle."""

    __slots__ = ("close", "high", "low", "open", "spread")

    def __init__(self, bars: pd.DataFrame) -> None:
        arrays = columns_as_arrays(bars)
        self.open = arrays["open"]
        self.high = arrays["high"]
        self.low = arrays["low"]
        self.close = arrays["close"]
        self.spread = arrays.get("spread")

    def spread_at(self, index: int) -> float | None:
        return None if self.spread is None else float(self.spread[index])


class _EquityTrack:
    """Buffer preasignado de la curva de equity."""

    __slots__ = ("balance", "equity", "exposure", "length")

    def __init__(self, n: int) -> None:
        self.balance = np.empty(n, dtype=float)
        self.equity = np.empty(n, dtype=float)
        self.exposure = np.zeros(n, dtype=np.int16)
        self.length = n

    def record(self, i: int, balance: float, equity: float, open_positions: int) -> None:
        self.balance[i] = balance
        self.equity[i] = equity
        self.exposure[i] = open_positions

    def truncate(self, length: int) -> None:
        self.length = length

    def to_frame(self, index: pd.DatetimeIndex) -> pd.DataFrame:
        n = self.length
        return pd.DataFrame(
            {
                "balance": self.balance[:n],
                "equity": self.equity[:n],
                "exposure": self.exposure[:n],
            },
            index=index[:n],
        )
