"""Esquemas Pydantic para los ficheros YAML de configuración.

Aquí vive toda la tolerancia al mundo exterior: tipos laxos, valores por
defecto y mensajes de error legibles. Hacia dentro solo cruzan objetos de
dominio y de aplicación ya validados.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chronos.application.backtest.config import (
    AccountConfig,
    BacktestConfig,
    DataConfig,
    ExecutionConfig,
    ReportingConfig,
    RiskConfig,
)
from chronos.domain.enums import Timeframe
from chronos.domain.instrument import CostModel, InstrumentSpec, SessionSpec, SwapModel


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Instrumento ------------------------------------------------------------


class CostsSchema(_Strict):
    spread_mode: Literal["fixed", "from_data"] = "fixed"
    spread_points: float = Field(default=20.0, ge=0)
    commission_per_lot_per_side: float = Field(default=3.0, ge=0)
    slippage_points: float = Field(default=1.0, ge=0)

    def to_domain(self) -> CostModel:
        return CostModel(**self.model_dump())


class SwapSchema(_Strict):
    mode: Literal["points"] = "points"
    long_points: float = 0.0
    short_points: float = 0.0
    triple_swap_weekday: int = Field(default=2, ge=0, le=6)
    rollover_hour_server: int = Field(default=0, ge=0, le=23)

    def to_domain(self) -> SwapModel:
        return SwapModel(**self.model_dump())


class DailyBreakSchema(_Strict):
    start: time
    end: time


class WeekBoundarySchema(_Strict):
    weekday: int = Field(ge=0, le=6)
    time: time


class SessionSchema(_Strict):
    timezone: str = "Europe/Athens"
    daily_break: DailyBreakSchema | None = None
    week_open: WeekBoundarySchema | None = None
    week_close: WeekBoundarySchema | None = None

    def to_domain(self) -> SessionSpec:
        defaults = SessionSpec()
        return SessionSpec(
            timezone=self.timezone,
            break_start=self.daily_break.start if self.daily_break else None,
            break_end=self.daily_break.end if self.daily_break else None,
            week_open_weekday=self.week_open.weekday if self.week_open else defaults.week_open_weekday,
            week_open_time=self.week_open.time if self.week_open else defaults.week_open_time,
            week_close_weekday=(
                self.week_close.weekday if self.week_close else defaults.week_close_weekday
            ),
            week_close_time=self.week_close.time if self.week_close else defaults.week_close_time,
        )


class InstrumentSchema(_Strict):
    symbol: str
    description: str = ""
    digits: int = Field(default=2, ge=0, le=8)
    tick_size: float = Field(default=0.01, gt=0)
    pip_size: float = Field(default=0.01, gt=0)
    contract_size: float = Field(default=100.0, gt=0)
    base_currency: str = "XAU"
    quote_currency: str = "USD"
    profit_currency: str = "USD"
    min_lot: float = Field(default=0.01, gt=0)
    max_lot: float = Field(default=100.0, gt=0)
    lot_step: float = Field(default=0.01, gt=0)
    leverage: float = Field(default=200.0, gt=0)
    costs: CostsSchema = Field(default_factory=CostsSchema)
    swap: SwapSchema = Field(default_factory=SwapSchema)
    session: SessionSchema = Field(default_factory=SessionSchema)

    def to_domain(self) -> InstrumentSpec:
        return InstrumentSpec(
            symbol=self.symbol,
            digits=self.digits,
            tick_size=self.tick_size,
            pip_size=self.pip_size,
            contract_size=self.contract_size,
            base_currency=self.base_currency,
            quote_currency=self.quote_currency,
            profit_currency=self.profit_currency,
            min_lot=self.min_lot,
            max_lot=self.max_lot,
            lot_step=self.lot_step,
            leverage=self.leverage,
            costs=self.costs.to_domain(),
            swap=self.swap.to_domain(),
            session=self.session.to_domain(),
        )


# --- Backtest ---------------------------------------------------------------


class AccountSchema(_Strict):
    initial_balance: float = Field(default=10_000.0, gt=0)
    currency: str = "USD"
    leverage: float = Field(default=200.0, gt=0)
    stop_out_level: float = Field(default=0.5, gt=0)
    margin_call_level: float = Field(default=1.0, gt=0)

    def to_domain(self) -> AccountConfig:
        return AccountConfig(**self.model_dump())


class DataSchema(_Strict):
    source: Literal["parquet", "csv", "synthetic"] = "parquet"
    path: str = ""
    timeframe: str = "M1"
    strategy_timeframe: str = "M15"
    start: datetime | None = None
    end: datetime | None = None
    timezone: str = "UTC"

    def to_domain(self) -> DataConfig:
        return DataConfig(
            source=self.source,
            path=self.path,
            timeframe=Timeframe.parse(self.timeframe),
            strategy_timeframe=Timeframe.parse(self.strategy_timeframe),
            start=self.start,
            end=self.end,
            timezone=self.timezone,
        )


class ExecutionSchema(_Strict):
    fill_model: Literal["next_bar_open", "current_close"] = "next_bar_open"
    intrabar_priority: Literal["worst", "best", "sl_first", "tp_first"] = "worst"
    price_basis: Literal["bid", "mid"] = "bid"
    apply_spread: bool = True
    apply_commission: bool = True
    apply_swap: bool = True
    apply_slippage: bool = True

    def to_domain(self) -> ExecutionConfig:
        return ExecutionConfig(**self.model_dump())


class RiskSchema(_Strict):
    sizing: Literal["fixed_lot", "fixed_fractional", "fixed_cash_risk"] = "fixed_fractional"
    risk_per_trade: float = Field(default=0.005, gt=0, lt=1)
    fixed_lot: float = Field(default=0.10, gt=0)
    fixed_cash_risk: float = Field(default=100.0, gt=0)
    max_lot: float = Field(default=5.0, gt=0)
    max_concurrent_positions: int = Field(default=1, ge=1)
    max_daily_loss: float | None = Field(default=0.03, gt=0)
    max_drawdown_stop: float | None = Field(default=0.25, gt=0)

    def to_domain(self) -> RiskConfig:
        return RiskConfig(**self.model_dump())


class ReportingSchema(_Strict):
    output_dir: str = "reports"
    save_trades_csv: bool = True
    save_equity_csv: bool = True
    html_report: bool = True
    max_price_bars: int = Field(default=20_000, ge=0)

    def to_domain(self) -> ReportingConfig:
        return ReportingConfig(**self.model_dump())


class StrategySchema(_Strict):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class BacktestSchema(_Strict):
    mode: Literal["backtest", "demo", "live"] = "backtest"
    instrument: str = "xauusd"
    account: AccountSchema = Field(default_factory=AccountSchema)
    data: DataSchema = Field(default_factory=DataSchema)
    execution: ExecutionSchema = Field(default_factory=ExecutionSchema)
    risk: RiskSchema = Field(default_factory=RiskSchema)
    strategy: StrategySchema
    reporting: ReportingSchema = Field(default_factory=ReportingSchema)
    random_seed: int = 42

    @field_validator("mode")
    @classmethod
    def _only_backtest_for_now(cls, value: str) -> str:
        # Puerta de seguridad: el proyecto todavía no tiene adaptador de bróker.
        # Pasar a demo/live exige implementarlo y quitar esta comprobación de forma
        # deliberada, no por editar un YAML.
        if value != "backtest":
            raise ValueError(
                "Solo 'backtest' está soportado. Demo y live requieren el adaptador "
                "de cTrader, que aún no forma parte del proyecto."
            )
        return value

    def to_domain(self) -> BacktestConfig:
        return BacktestConfig(
            instrument=self.instrument,
            account=self.account.to_domain(),
            execution=self.execution.to_domain(),
            risk=self.risk.to_domain(),
            data=self.data.to_domain(),
            reporting=self.reporting.to_domain(),
            strategy_name=self.strategy.name,
            strategy_params=dict(self.strategy.params),
            random_seed=self.random_seed,
        )
