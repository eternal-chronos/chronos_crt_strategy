"""Objetos de configuración de una corrida. Dataclasses puras: la validación de
YAML/entorno vive en la capa de infraestructura.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from chronos.domain.enums import Timeframe
from chronos.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class AccountConfig:
    initial_balance: float = 10_000.0
    currency: str = "USD"
    leverage: float = 200.0
    stop_out_level: float = 0.5
    margin_call_level: float = 1.0


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Cómo se traducen las intenciones de la estrategia en fills."""

    fill_model: str = "next_bar_open"  # next_bar_open | current_close
    intrabar_priority: str = "worst"  # worst | best | sl_first | tp_first
    # Qué representa el precio del dataset: "bid" (típico en brokers) o "mid".
    price_basis: str = "bid"
    apply_spread: bool = True
    apply_commission: bool = True
    apply_swap: bool = True
    apply_slippage: bool = True

    def __post_init__(self) -> None:
        if self.fill_model not in ("next_bar_open", "current_close"):
            raise DomainError(f"fill_model desconocido: {self.fill_model}")
        if self.intrabar_priority not in ("worst", "best", "sl_first", "tp_first"):
            raise DomainError(f"intrabar_priority desconocido: {self.intrabar_priority}")
        if self.price_basis not in ("bid", "mid"):
            raise DomainError(f"price_basis desconocido: {self.price_basis}")


@dataclass(frozen=True, slots=True)
class RiskConfig:
    sizing: str = "fixed_fractional"  # fixed_lot | fixed_fractional | fixed_cash_risk
    risk_per_trade: float = 0.005
    fixed_lot: float = 0.10
    fixed_cash_risk: float = 100.0
    max_lot: float = 5.0
    max_concurrent_positions: int = 1
    max_daily_loss: float | None = 0.03
    max_drawdown_stop: float | None = 0.25

    def __post_init__(self) -> None:
        if self.sizing not in ("fixed_lot", "fixed_fractional", "fixed_cash_risk"):
            raise DomainError(f"Política de sizing desconocida: {self.sizing}")
        if not 0 < self.risk_per_trade < 1:
            raise DomainError("risk_per_trade debe estar en (0, 1)")
        if self.max_concurrent_positions < 1:
            raise DomainError("max_concurrent_positions debe ser >= 1")


@dataclass(frozen=True, slots=True)
class DataConfig:
    source: str = "parquet"
    path: str = ""
    timeframe: Timeframe = Timeframe.M1
    strategy_timeframe: Timeframe = Timeframe.M15
    start: datetime | None = None
    end: datetime | None = None
    timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class ReportingConfig:
    output_dir: str = "reports"
    save_trades_csv: bool = True
    save_equity_csv: bool = True
    html_report: bool = True
    max_price_bars: int = 20_000


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Configuración completa de una corrida de backtest."""

    instrument: str = "xauusd"
    account: AccountConfig = field(default_factory=AccountConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    data: DataConfig = field(default_factory=DataConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    strategy_name: str = ""
    strategy_params: dict[str, object] = field(default_factory=dict)
    random_seed: int = 42
